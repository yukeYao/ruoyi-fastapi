"""
智能助手路由，路径前缀 /agent。所有接口都要求 agent:chat:use（菜单"智能助手"的权限字符）；
工具层面的权限由 Agent 在预览和执行每个工具前单独校验。

操作日志：对话是查询，不记录；审批接口不用 @log 装饰，而是在 execute 节点真正执行写操作时，
用 record_oper_log 写一条以"智能助手"为来源的日志（参数是 actionId 和校验后的参数，URL 是审批接口）。
这样一次审批只对应一条业务日志，且日志记录的是实际执行的操作，而不是"点了批准按钮"。
拒绝不改业务数据，审批记录保存在 agent_action（decided_by / decided_time）。
"""
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.agent import model as agent_model
from app.core.constants import AGENT_CHAT_PERM, DECISION_APPROVE, DECISION_REJECT
from app.core.database import get_db
from app.core.deps import has_perm
from app.core.request_utils import get_client_ip
from app.core.response import ajax_success, table_data_info
from app.schemas.agent import AgentChatRequest
from app.services import agent_service

router = APIRouter(prefix="/agent")

# SSE 响应头：禁止缓存；X-Accel-Buffering 让 Nginx 等反向代理不要缓冲，事件才能实时到达
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _sse_response(run) -> StreamingResponse:
    # 生成器的 finally 会释放运行锁；客户端在推流开始前就断开时生成器可能根本不会被执行，
    # 所以再挂一个后台任务兜底释放（释放是"比较值再删除"，重复调用无害）
    return StreamingResponse(agent_service.stream_run(run), media_type="text/event-stream", headers=_SSE_HEADERS,
                             background=BackgroundTask(agent_service.release_run, run))


@router.post("/chat")
def chat(
    body: AgentChatRequest,
    request: Request,
    db: Session = Depends(get_db),
    login_user: dict = Depends(has_perm(AGENT_CHAT_PERM)),
):
    # 通过模块属性取工厂，测试才能用 monkeypatch 替换成 ScriptedChatModel
    model = agent_model.get_chat_model()
    return ajax_success(agent_service.chat(db, login_user, body.conversation_id, body.message, model, get_client_ip(request)))


# 固定路径 /chat/stream 与 /chat 不冲突；预检（登录、权限、会话归属、运行锁）在返回 StreamingResponse 之前完成，
# 出错时仍是 HTTP 200 + JSON，和其他接口一样由前端按 code 处理
@router.post("/chat/stream")
def chat_stream(
    body: AgentChatRequest,
    request: Request,
    db: Session = Depends(get_db),
    login_user: dict = Depends(has_perm(AGENT_CHAT_PERM)),
):
    model = agent_model.get_chat_model()
    run = agent_service.prepare_chat(db, login_user, body.conversation_id, body.message, model, get_client_ip(request))
    return _sse_response(run)


@router.get("/conversation/list")
def list_conversations(
    pageNum: int = Query(default=1, ge=1),
    pageSize: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_db),
    login_user: dict = Depends(has_perm(AGENT_CHAT_PERM)),
):
    rows, total = agent_service.list_conversations(db, login_user, pageNum, pageSize)
    return table_data_info(rows, total)


@router.get("/conversation/{conversation_id}/messages")
def get_messages(conversation_id: str, db: Session = Depends(get_db), login_user: dict = Depends(has_perm(AGENT_CHAT_PERM))):
    messages, pending_action = agent_service.get_messages(db, login_user, conversation_id)
    return ajax_success(messages, pendingAction=pending_action)


@router.get("/action/{action_id}")
def get_action(action_id: str, db: Session = Depends(get_db), login_user: dict = Depends(has_perm(AGENT_CHAT_PERM))):
    return ajax_success(agent_service.get_action_detail(db, login_user, action_id))


# 审批接口不声明请求体：只接受路径里的 action_id 和接口本身代表的决定，客户端无法提交或修改参数
@router.post("/action/{action_id}/approve")
def approve_action(
    action_id: str, request: Request, db: Session = Depends(get_db), login_user: dict = Depends(has_perm(AGENT_CHAT_PERM))
):
    model = agent_model.get_chat_model()
    return ajax_success(agent_service.decide(db, login_user, action_id, DECISION_APPROVE, model, get_client_ip(request)))


@router.post("/action/{action_id}/reject")
def reject_action(
    action_id: str, request: Request, db: Session = Depends(get_db), login_user: dict = Depends(has_perm(AGENT_CHAT_PERM))
):
    model = agent_model.get_chat_model()
    return ajax_success(agent_service.decide(db, login_user, action_id, DECISION_REJECT, model, get_client_ip(request)))


@router.post("/action/{action_id}/approve/stream")
def approve_action_stream(
    action_id: str, request: Request, db: Session = Depends(get_db), login_user: dict = Depends(has_perm(AGENT_CHAT_PERM))
):
    model = agent_model.get_chat_model()
    run = agent_service.prepare_decide(db, login_user, action_id, DECISION_APPROVE, model, get_client_ip(request))
    return _sse_response(run)


@router.post("/action/{action_id}/reject/stream")
def reject_action_stream(
    action_id: str, request: Request, db: Session = Depends(get_db), login_user: dict = Depends(has_perm(AGENT_CHAT_PERM))
):
    model = agent_model.get_chat_model()
    run = agent_service.prepare_decide(db, login_user, action_id, DECISION_REJECT, model, get_client_ip(request))
    return _sse_response(run)
