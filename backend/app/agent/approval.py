"""
写操作审批的两端：propose（生成 PENDING action）和 execute（批准后执行）。

LangGraph 恢复执行时，被 interrupt 的节点会从头重跑，所以：
- propose 节点完成全部校验并以 tool_call_id 幂等写入 agent_action，重跑只会查到同一条；
- approval 节点只调用 interrupt，不写库、不产生副作用（见 graph.py）；
- execute 节点先用条件 UPDATE 把 APPROVED 改成 EXECUTING，抢不到就不执行，
  所以重复点击、并发请求、重跑都不会重复执行写操作；
- 停在 EXECUTING 的操作（执行中途崩溃）不会自动重试，只提示需要人工确认。
执行一律使用数据库里保存的参数（并校验参数哈希），不使用 resume 传入的任何内容。
"""
import json
import logging
import time
from datetime import datetime

from langchain_core.messages import AIMessage, ToolMessage

from app.agent.context import AgentContext
from app.agent.policy import (
    MSG_INVALID_JSON,
    MSG_NO_PERMISSION,
    MSG_ONE_WRITE_AT_A_TIME,
    MSG_TOO_MANY_CALLS,
    MSG_UNKNOWN_TOOL,
    error_message,
    is_write_call,
    parse_args,
)
from app.agent.serialization import strip_forbidden
from app.agent.tools import TOOLS
from app.core.constants import (
    ACTION_APPROVED,
    ACTION_EXECUTING,
    ACTION_EXPIRED,
    ACTION_FAILED,
    ACTION_PENDING,
    ACTION_REJECTED,
    ACTION_SUCCEEDED,
    AGENT_MAX_TOOL_CALLS_PER_STEP,
    AGENT_OPER_LOG_SOURCE,
    BIZ_OTHER,
    OPER_STATUS_FAIL,
    OPER_STATUS_SUCCESS,
)
from app.core.database import SessionLocal
from app.core.deps import check_perm
from app.core.exceptions import ServiceException
from app.core.oper_log import record_oper_log
from app.services import agent_action_service as actions

logger = logging.getLogger("ruoyi.agent")

MSG_PREVIEW_FAILED = "生成操作预览失败"
MSG_EXECUTED = "操作已执行成功"
MSG_REJECTED = "用户已拒绝，不要重试"
MSG_EXPIRED = "审批已过期，操作未执行；如仍需要，请重新提交"
MSG_NOT_APPROVED = "操作未获批准，未执行"
MSG_EXECUTION_UNKNOWN = "该操作上次执行中途中断，结果未知，不会自动重试，请人工确认后再处理"
MSG_ACTION_MISMATCH = "审批记录与当前会话不一致，操作未执行"
MSG_ARGS_TAMPERED = "操作参数校验失败，操作未执行"
MSG_TOOL_MISMATCH = "工具定义不一致，操作未执行"
MSG_EXECUTE_FAILED = "执行失败"


# ---------------------------------------------------------------------------
# propose
# ---------------------------------------------------------------------------

def _propose_one(ctx: AgentContext, conversation_id: str, call: dict) -> tuple[ToolMessage | None, str | None]:
    call_id, name = call["id"], call["name"]
    spec = TOOLS[name]
    try:
        scoped_user = check_perm(ctx.login_user, spec.perm)
    except ServiceException:
        return error_message(call_id, name, MSG_NO_PERMISSION), None
    args, error = parse_args(spec, call)
    if error:
        return error_message(call_id, name, error), None

    db = SessionLocal()
    try:
        # 预览完全由服务端查询生成：模型只能提供参数（已校验），预览文案、风险说明、所需权限都不来自模型
        preview = {**spec.preview(db, scoped_user, args, ctx.session_token), "perm": spec.perm, "toolName": name}
        # 冻结服务端解析出的目标：只取 userId、userName，模型无法提供或修改（入参 schema 里没有这个字段）
        target = {"userId": preview["target"]["userId"], "userName": preview["target"]["userName"]}
        args_json = actions.canonical_json(args.model_dump())
        action = actions.create_action_once(db, conversation_id, ctx.login_user["user_id"], call_id, name, args_json,
                                            target, preview, spec.perm)
        if action.user_id != ctx.login_user["user_id"] or action.tool_name != name:
            return error_message(call_id, name, MSG_ACTION_MISMATCH), None
        return None, action.action_id
    except ServiceException as exc:
        db.rollback()
        return error_message(call_id, name, exc.message), None
    except Exception:
        db.rollback()
        logger.exception("生成写操作预览失败：%s", name)
        return error_message(call_id, name, MSG_PREVIEW_FAILED), None
    finally:
        db.close()


def propose(ctx: AgentContext, conversation_id: str, message: AIMessage) -> dict:
    """
    处理一条只包含写调用（和未知调用）的 AIMessage：只有第一个写调用进入审批，其余写调用返回
    "请逐个提交"。进入审批的那个调用暂时不回复，等 execute 节点给出结果（批准/拒绝/过期）再回复；
    其他每个 tool_call 都在这里回复，保证没有遗漏的 tool_call。
    """
    results: list[ToolMessage] = []
    pending_action_id = pending_call_id = None
    chosen = False
    for index, call in enumerate(message.tool_calls):
        call_id, name = call.get("id") or "", call.get("name") or ""
        if index >= AGENT_MAX_TOOL_CALLS_PER_STEP:
            results.append(error_message(call_id, name, MSG_TOO_MANY_CALLS))
        elif not is_write_call(call):
            results.append(error_message(call_id, name, MSG_UNKNOWN_TOOL))
        elif chosen:
            results.append(error_message(call_id, name, MSG_ONE_WRITE_AT_A_TIME))
        else:
            chosen = True
            error, action_id = _propose_one(ctx, conversation_id, call)
            if error is not None:
                results.append(error)
            else:
                pending_action_id, pending_call_id = action_id, call_id
    for bad in message.invalid_tool_calls:
        results.append(error_message(bad.get("id") or "", bad.get("name") or "", MSG_INVALID_JSON))
    return {"messages": results, "pending_action_id": pending_action_id, "pending_tool_call_id": pending_call_id}


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------

# 各状态对应的服务端说明（可信字段）。executed：True 已执行成功；False 未执行；None 结果未知
_OUTCOMES = {
    ACTION_SUCCEEDED: (True, MSG_EXECUTED),
    ACTION_REJECTED: (False, MSG_REJECTED),
    ACTION_EXPIRED: (False, MSG_EXPIRED),
    ACTION_PENDING: (False, MSG_NOT_APPROVED),
    ACTION_APPROVED: (False, MSG_NOT_APPROVED),
    ACTION_EXECUTING: (None, MSG_EXECUTION_UNKNOWN),
    ACTION_FAILED: (False, MSG_EXECUTE_FAILED),
}


def outcome_payload(action) -> dict:
    """
    审批结果的 ToolMessage 内容，分两层：
    - 外层是服务端可信字段：ok、executed、status、action_id、tool_name、message，全部由服务端根据
      agent_action 的状态生成，模型应该据此判断"有没有执行"。之前把执行状态和业务数据一起包在
      untrusted_data 里，真实模型把"已执行成功"总结成了"等待审批中"；
    - untrusted_data 里是业务数据：目标用户名、执行结果、失败详情。这些可能含用户可控内容
      （用户名、昵称等），只能当数据看。
    """
    executed, message = _OUTCOMES.get(action.status, (False, MSG_NOT_APPROVED))
    data = {"target": json.loads(action.target_json) if action.target_json else None}
    if action.status == ACTION_SUCCEEDED and action.result:
        data["result"] = json.loads(action.result)
    if action.status == ACTION_FAILED and action.error:
        data["errorDetail"] = action.error
    return {
        "ok": action.status == ACTION_SUCCEEDED,
        "executed": executed,
        "status": action.status,
        "action_id": action.action_id,
        "tool_name": action.tool_name,
        "message": message,
        "untrusted_data": strip_forbidden(data),
    }


def _outcome_message(action, tool_call_id: str) -> ToolMessage:
    """按 action 的最终状态给模型一条结果；这里不执行任何写操作"""
    payload = outcome_payload(action)
    return ToolMessage(
        content=json.dumps(payload, ensure_ascii=False, default=str),
        tool_call_id=tool_call_id,
        name=action.tool_name,
        status="success" if payload["ok"] else "error",
    )


def _run(db, ctx: AgentContext, action) -> None:
    """已抢到 EXECUTING 后真正执行：重新校验参数哈希、过期时间、权限和数据范围，再调用现有 service"""
    spec = TOOLS.get(action.tool_name)
    start = time.time()
    result, error = None, ""
    try:
        if spec is None or spec.execute is None or spec.perm != action.perm:
            raise ServiceException(MSG_TOOL_MISMATCH)
        if actions.hash_action(action.args_json, action.target_json) != action.args_hash:
            raise ServiceException(MSG_ARGS_TAMPERED)
        if actions.is_expired(action):
            raise ServiceException(MSG_EXPIRED)
        args = spec.args_model.model_validate_json(action.args_json)
        # 用本次请求重新解析的登录态校验权限：等待审批期间权限被撤销，重新登录后这里会被拒绝
        scoped_user = check_perm(ctx.login_user, spec.perm)
        result = strip_forbidden(spec.execute(db, scoped_user, args, ctx.session_token, json.loads(action.target_json)))
        final_status = ACTION_SUCCEEDED
    except ServiceException as exc:
        db.rollback()
        final_status, error = ACTION_FAILED, exc.message
    except Exception:
        db.rollback()
        logger.exception("智能助手写操作执行异常：%s", action.action_id)
        final_status, error = ACTION_FAILED, MSG_EXECUTE_FAILED

    now = datetime.now()
    result_json = json.dumps(result, ensure_ascii=False, default=str) if result is not None else ""
    actions.transition(db, action.action_id, (ACTION_EXECUTING,), final_status,
                       finish_time=now, result=result_json[:2000], error=error[:2000])
    # 进入现有操作日志：method 以"智能助手"开头标记来源；参数只记 actionId、工具名和校验后的参数，
    # 结果只记服务端整理过的 result（已剔除 tokenId 等字段）
    record_oper_log(
        spec.oper_title if spec else AGENT_OPER_LOG_SOURCE,
        spec.business_type if spec else BIZ_OTHER,
        ctx.login_user,
        {"actionId": action.action_id, "toolName": action.tool_name, "args": json.loads(action.args_json),
         "target": json.loads(action.target_json)},
        {"status": final_status, "result": result} if final_status == ACTION_SUCCEEDED else None,
        OPER_STATUS_SUCCESS if final_status == ACTION_SUCCEEDED else OPER_STATUS_FAIL,
        error,
        int((time.time() - start) * 1000),
        method=f"{AGENT_OPER_LOG_SOURCE}:{action.tool_name}",
        request_method="POST",
        url=f"/agent/action/{action.action_id}/approve",
        ip=ctx.client_ip,
        background=False,
    )


def execute(ctx: AgentContext, conversation_id: str, action_id: str | None, tool_call_id: str | None) -> dict:
    db = SessionLocal()
    try:
        action = actions.get_action(db, action_id) if action_id else None
        # 归属再校验一次：action 必须属于当前登录用户、当前会话
        if action is None or action.user_id != ctx.login_user["user_id"] or action.conversation_id != conversation_id:
            message = error_message(tool_call_id or "", "unknown", MSG_ACTION_MISMATCH)
        else:
            if action.status == ACTION_APPROVED and actions.transition(
                db, action.action_id, (ACTION_APPROVED,), ACTION_EXECUTING, execute_start_time=datetime.now()
            ):
                _run(db, ctx, action)
            # 没抢到（已被别的请求执行、已拒绝/过期、停在 EXECUTING）就只汇报数据库里的状态，不再执行
            message = _outcome_message(actions.get_action(db, action.action_id), tool_call_id or action.tool_call_id)
    finally:
        db.close()
    return {"messages": [message], "pending_action_id": None, "pending_tool_call_id": None}
