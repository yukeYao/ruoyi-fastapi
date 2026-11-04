"""
端到端运行器：走真实的 /agent/chat（或 /agent/chat/stream）接口，按方案限制工具、切换检索方式。

方案（variant）
- no_rag：业务工具都在，没有 search_knowledge（无知识库基线）
- bm25 / vector / hybrid：只有 search_knowledge，检索方式分别为 BM25、向量、混合
- hybrid_tools：混合检索 + 全部业务工具（当前线上方案）
工具限制用 AgentContext.tool_names（只会在权限过滤基础上收窄，硬调用也被拒绝），检索方式用
settings.knowledge_retrieval_mode；两者都只在评估进程里设置，不改任何持久化配置。

只有知识库工具的方案不运行需要业务工具的类别（实时排查、权限建议、注入——注入需要写工具才有意义），
无知识库基线不运行依赖检索结果的类别（受限知识、伪造引用、追问、SSE 一致性），报告里标"不适用"并写明原因。

本模块导入即连接数据库：必须先由 run.py 设置测试库环境变量。
"""
import json
import time
from dataclasses import dataclass

from langchain_core.messages import AIMessage

from app.agent.graph import MSG_STEP_LIMIT, MSG_TIME_LIMIT, get_graph
from app.agent.tools import TOOLS
from app.config import settings
from app.core.constants import AGENT_RATE_LIMIT_KEY_PREFIX, LOGIN_TOKEN_KEY_PREFIX, PWD_ERR_CNT_KEY_PREFIX
from app.rag.retriever import Thresholds
from app.services import agent_service
from evals import environment as env
from evals.rag_smoke import SmokeRecords
from evals.runners import _usage

SEARCH = "search_knowledge"
KNOWLEDGE_ONLY = ("rule", "no_answer", "restricted", "forged", "followup", "sse")
NO_RAG = ("rule", "no_answer", "live", "perm_advice")
ALL = ("rule", "live", "no_answer", "restricted", "injection", "forged", "followup", "sse", "perm_advice")


@dataclass(frozen=True)
class Variant:
    name: str
    title: str
    mode: str
    tools: frozenset[str] | None
    categories: tuple[str, ...]


VARIANTS = {
    "no_rag": Variant("no_rag", "无 RAG（业务工具，无知识库）", "hybrid", frozenset(TOOLS) - {SEARCH}, NO_RAG),
    "bm25": Variant("bm25", "BM25（仅知识库）", "bm25", frozenset({SEARCH}), KNOWLEDGE_ONLY),
    "vector": Variant("vector", "向量检索（仅知识库）", "vector", frozenset({SEARCH}), KNOWLEDGE_ONLY),
    "hybrid": Variant("hybrid", "混合检索（仅知识库）", "hybrid", frozenset({SEARCH}), KNOWLEDGE_ONLY),
    "hybrid_tools": Variant("hybrid_tools", "混合检索 + 业务工具", "hybrid", None, ALL),
}

_VARIANT_SETTINGS = ("knowledge_retrieval_mode", "knowledge_min_vector_score", "knowledge_min_bm25_score",
                     "knowledge_min_bm25_terms")

PASSWORD = "Smoke@12345"
VICTIM = "smoke_ev_victim"


# ---------------------------------------------------------------------------
# 测试数据（smoke_ev_ 前缀，结束时由 rag_smoke.cleanup_smoke_data 按记录物理删除）
# ---------------------------------------------------------------------------

def prepare_data(records: SmokeRecords) -> dict[str, dict]:
    """建评估用的角色、用户、操作者；返回 {操作者: 请求头}"""
    admin = env.login("admin", "admin123")
    records.logins.append(admin)

    def add_role(name: str, perms: list[str], status: str = "0") -> int:
        body = {"roleName": name, "roleKey": name, "roleSort": 90, "status": "0", "menuIds": env._menu_ids(perms)}
        assert env.client.post("/system/role", headers=admin, json=body).json()["code"] == 200
        role_id = env.sql("SELECT role_id FROM sys_role WHERE role_key = :k AND del_flag = '0'", k=name)[0][0]
        records.role_ids.append(role_id)
        if status != "0":
            assert env.client.put("/system/role/changeStatus", headers=admin,
                                  json={"roleId": role_id, "status": status}).json()["code"] == 200
        return role_id

    def add_user(name: str, role_ids: list[int]) -> int:
        body = {"userName": name, "nickName": name, "password": PASSWORD, "deptId": 105, "postIds": [], "roleIds": role_ids}
        assert env.client.post("/system/user", headers=admin, json=body).json()["code"] == 200
        user_id = env.sql("SELECT user_id FROM sys_user WHERE user_name = :n AND del_flag = '0'", n=name)[0][0]
        records.user_ids.append(user_id)
        return user_id

    add_user("smoke_ev_noagent", [add_role("smoke_ev_noagent_r", ["system:user:list"])])
    add_user("smoke_ev_agent", [add_role("smoke_ev_agent_r", ["agent:chat:use"])])
    add_user("smoke_ev_disrole", [add_role("smoke_ev_disrole_r", ["agent:chat:use"], status="1")])
    add_user("smoke_ev_locked", [])
    add_user(VICTIM, [])
    add_user("smoke_ev_plain", [add_role("smoke_ev_plain_r", ["agent:chat:use"])])
    add_user("smoke_ev_noforce", [add_role("smoke_ev_noforce_r", ["agent:chat:use", "system:user:list",
                                                                  "system:user:query", "monitor:online:list"])])
    headers = {"admin": admin}
    for name in ("smoke_ev_plain", "smoke_ev_noforce"):
        headers[name] = env.login(name, PASSWORD)
        records.logins.append(headers[name])
    records.operator_ids |= {1, *[env.sql("SELECT user_id FROM sys_user WHERE user_name = :n AND del_flag = '0'", n=n)[0][0]
                                  for n in ("smoke_ev_plain", "smoke_ev_noforce")]}
    return headers


def reset_state(records: SmokeRecords) -> None:
    """每条用例前：清频率限制计数、恢复注入目标的状态和在线会话、锁定账号的错误次数"""
    for user_id in records.operator_ids:
        env.redis_client.delete(AGENT_RATE_LIMIT_KEY_PREFIX + str(int(user_id)))
    env.sql("UPDATE sys_user SET status = '0' WHERE user_name = :n", n=VICTIM)
    if not _sessions(VICTIM):
        records.logins.append(env.login(VICTIM, PASSWORD))
    env.redis_client.set(PWD_ERR_CNT_KEY_PREFIX + "smoke_ev_locked", 5, ex=600)


def _sessions(user_name: str) -> int:
    count = 0
    for key in env.redis_client.scan_iter(LOGIN_TOKEN_KEY_PREFIX + "*"):
        value = env.redis_client.get(key)
        if value and json.loads(value).get("user_name") == user_name:
            count += 1
    return count


def _victim_state() -> tuple[str, int]:
    return env.sql("SELECT status FROM sys_user WHERE user_name = :n AND del_flag = '0'", n=VICTIM)[0][0], _sessions(VICTIM)


# ---------------------------------------------------------------------------
# 运行一条用例
# ---------------------------------------------------------------------------

def _post(headers: dict, message: str, conversation_id: str | None, sse: bool) -> dict:
    body = {"message": message, **({"conversationId": conversation_id} if conversation_id else {})}
    if not sse:
        resp = env.client.post("/agent/chat", headers=headers, json=body).json()
        if resp.get("code") != 200:
            raise RuntimeError(f"对话接口返回 {resp.get('code')}：{resp.get('msg')}")
        return resp["data"]
    text = env.client.post("/agent/chat/stream", headers=headers, json=body).text
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        events.append((lines.get("event"), json.loads(lines.get("data", "{}"))))
    final = next((d for e, d in reversed(events) if e == "final"), None)
    if final is None:
        error = next((d for e, d in events if e == "error"), {})
        raise RuntimeError(f"SSE 没有 final 事件：{error.get('msg')}")
    return final


def _turn_record(data: dict) -> dict:
    calls = [c for m in data["messages"] if m["type"] == "ai" for c in m["toolCalls"]]
    # 引用修复已移除（v3）：接口不再返回 autoSources / citationRepair，这里固定为空，评分与 v2 的字段保持一致
    return {"reply": data["reply"], "sources": data["sources"], "autoSources": data.get("autoSources", []),
            "references": data["references"], "invalidCitations": data["invalidCitations"],
            "suspiciousCitations": data["suspiciousCitations"], "knowledgeStatus": data["knowledgeStatus"],
            "citationRepair": data.get("citationRepair"), "pendingAction": data["pendingAction"],
            "searched": any(c["name"] == SEARCH for c in calls)}


def _count_model_usage(trace: dict, conversation_id: str) -> None:
    state = get_graph().get_state({"configurable": {"thread_id": conversation_id}})
    for m in state.values.get("messages", []):
        if not isinstance(m, AIMessage) or (m.content in (MSG_STEP_LIMIT, MSG_TIME_LIMIT) and not m.tool_calls):
            continue
        name = (m.response_metadata or {}).get("model_name")
        if name:
            trace["model_calls"] += 1
            trace["model_names"] = sorted(set(trace["model_names"]) | {name})
            tin, tout = _usage(m)
            trace["input_tokens"] += tin
            trace["output_tokens"] += tout
        repair = (m.additional_kwargs or {}).get("citation_repair") or {}
        if repair.get("model_name"):
            trace["model_calls"] += 1
            trace["repair_calls"] += 1
            trace["input_tokens"] += int(repair.get("input_tokens") or 0)
            trace["output_tokens"] += int(repair.get("output_tokens") or 0)


def run_case(case: dict, variant: Variant, run: int, headers: dict, thresholds: Thresholds, embedding_model,
             records: SmokeRecords) -> dict:
    trace = {"case_id": case["id"], "category": case["category"], "variant": variant.name, "run": run,
             "operator": case["operator"], "transport": case.get("transport", "normal"), "error": "", "turns": [],
             "tool_calls": [], "tool_results": [], "model_calls": 0, "repair_calls": 0, "model_names": [],
             "input_tokens": 0, "output_tokens": 0, "embedding_tokens": 0, "effects": []}
    # 方案只在这条用例运行期间生效：检索方式、门槛、工具白名单，结束后恢复原值
    saved = {k: getattr(settings, k) for k in _VARIANT_SETTINGS}
    settings.knowledge_retrieval_mode = variant.mode
    settings.knowledge_min_vector_score = thresholds.min_vector_score
    settings.knowledge_min_bm25_score = thresholds.min_bm25_score
    settings.knowledge_min_bm25_terms = thresholds.min_bm25_terms
    original_build = agent_service.build_context
    agent_service.build_context = lambda *a, **kw: original_build(*a, **kw, tool_names=variant.tools)
    reset_state(records)
    before = _victim_state()
    embed_before = getattr(embedding_model, "total_tokens", 0)
    started = time.monotonic()
    conversation_id = None
    try:
        for message in case["turns"]:
            data = _post(headers[case["operator"]], message, conversation_id, trace["transport"] == "sse")
            conversation_id = data["conversationId"]
            records.conversations.append(conversation_id)
            trace["turns"].append(_turn_record(data))
            for m in data["messages"]:
                if m["type"] == "ai":
                    trace["tool_calls"] += [{"name": c["name"], "args": c["args"]} for c in m["toolCalls"]]
                elif m["type"] == "tool":
                    try:
                        payload = json.loads(m["content"])
                    except ValueError:
                        payload = {"ok": False, "raw": m["content"][:200]}
                    trace["tool_results"].append({"name": m["name"], "payload": payload})
        if trace["transport"] == "sse":
            # SSE 的来源必须与历史接口里同一条回复的来源完全一致（都由服务端生成）
            history = env.client.get(f"/agent/conversation/{conversation_id}/messages",
                                     headers=headers[case["operator"]]).json()["data"]
            last_ai = [m for m in history if m["type"] == "ai" and m["content"]][-1]
            trace["history_sources"] = [s["chunkId"] for s in (last_ai.get("knowledge") or {}).get("sources", [])]
    except Exception as exc:  # 模型或网络错误：如实记为出错，不中断整轮评估
        trace["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    finally:
        agent_service.build_context = original_build
        for key, value in saved.items():
            setattr(settings, key, value)
    trace["latency"] = round(time.monotonic() - started, 2)
    trace["embedding_tokens"] = getattr(embedding_model, "total_tokens", 0) - embed_before
    if conversation_id:
        _count_model_usage(trace, conversation_id)
    after = _victim_state()
    if after[0] != before[0]:
        trace["effects"].append(["change_user_status", VICTIM])
    if after[1] < before[1]:
        trace["effects"].append(["force_logout_user", VICTIM])
    return trace
