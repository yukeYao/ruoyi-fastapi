"""智能助手工具与策略：读写混合、非法调用、上限、结果白名单与脱敏、聚合统计"""
import json
import time
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy import text

from app.agent import policy
from app.agent.graph import MSG_STEP_LIMIT
from app.agent.serialization import mask_email, mask_phone, tool_success
from app.core.constants import (
    AGENT_MAX_MODEL_STEPS,
    AGENT_MAX_TOOL_CALLS_PER_STEP,
    AGENT_MAX_TOOL_RESULT_CHARS,
    PWD_ERR_CNT_KEY_PREFIX,
)
from app.core.database import SessionLocal
from app.core.redis_client import redis_client
from main import app
from tests.agent_helpers import call, chat, final, tool_msg, tool_results
from tests.conftest import auth_headers

client = TestClient(app)


def _content(result):
    return json.loads(result["content"])


def _admin_chat(scripted_model, *script):
    fake = scripted_model(*script)
    body = chat(client, auth_headers(client), "测试")
    assert body["code"] == 200, body
    return body, fake


def test_mixed_read_and_write_only_runs_read(scripted_model):
    read, write = call("search_users", {"user_name": "ry"}), call("change_user_status", {"user_id": 2, "status": "1"})
    body, _ = _admin_chat(scripted_model, tool_msg(read, write), final())
    results = tool_results(body)
    assert set(results) == {read["id"], write["id"]}  # 每个 tool_call 都有 ToolMessage
    assert _content(results[read["id"]])["ok"] is True
    assert _content(results[write["id"]]) == {"ok": False, "error": policy.MSG_WRITE_WITH_READ}
    # 写操作确实没有执行
    db = SessionLocal()
    assert db.execute(text("SELECT status FROM sys_user WHERE user_id = 2")).scalar_one() == "0"
    db.close()


def test_write_only_is_not_executed_in_stage1(scripted_model):
    write = call("force_logout_user", {"user_name": "ry"})
    body, _ = _admin_chat(scripted_model, tool_msg(write), final())
    assert _content(tool_results(body)[write["id"]]) == {"ok": False, "error": policy.MSG_WRITE_NOT_OPEN}


def test_unknown_tool_and_invalid_args_are_handled(scripted_model):
    unknown = call("execute_sql", {"sql": "DROP TABLE sys_user"})
    too_big = call("search_users", {"page_size": 999})
    extra = call("search_users", {"password": "x"})
    bad_type = call("get_user_detail", {"user_id": "1 OR 1=1"})
    body, _ = _admin_chat(scripted_model, tool_msg(unknown, too_big, extra, bad_type), final())
    results = tool_results(body)
    assert _content(results[unknown["id"]])["error"] == policy.MSG_UNKNOWN_TOOL
    for c, field in ((too_big, "page_size"), (extra, "password"), (bad_type, "user_id")):
        error = _content(results[c["id"]])["error"]
        assert error.startswith(policy.MSG_INVALID_ARGS) and field in error
    # 错误信息不回显模型传入的参数值
    assert "1 OR 1=1" not in results[bad_type["id"]]["content"]


def test_invalid_json_tool_call_gets_response(scripted_model):
    broken = AIMessage(content="", invalid_tool_calls=[{"name": "search_users", "args": "{bad json", "id": "call_bad",
                                                        "error": "json", "type": "invalid_tool_call"}])
    body, _ = _admin_chat(scripted_model, broken, final())
    assert _content(tool_results(body)["call_bad"])["error"] == policy.MSG_INVALID_JSON


def test_too_many_tool_calls_in_one_step(scripted_model):
    calls = [call("list_roles") for _ in range(AGENT_MAX_TOOL_CALLS_PER_STEP + 2)]
    body, _ = _admin_chat(scripted_model, tool_msg(*calls), final())
    results = tool_results(body)
    assert len(results) == len(calls)
    executed = [c for c in calls if _content(results[c["id"]])["ok"]]
    blocked = [c for c in calls if _content(results[c["id"]]).get("error") == policy.MSG_TOO_MANY_CALLS]
    assert len(executed) == AGENT_MAX_TOOL_CALLS_PER_STEP and len(blocked) == 2


def test_step_limit_stops_endless_tool_loop(scripted_model):
    # 模型每一步都继续调用工具，永远不收尾
    looping = [lambda msgs, tools: tool_msg(call("list_roles")) for _ in range(AGENT_MAX_MODEL_STEPS + 5)]
    body, fake = _admin_chat(scripted_model, *looping)
    assert body["data"]["reply"] == MSG_STEP_LIMIT
    assert len(fake.calls) == AGENT_MAX_MODEL_STEPS


def test_tool_result_truncated_but_valid_json():
    rows = [{"userName": "u" * 100, "n": i} for i in range(200)]
    text_ = tool_success({"total": 200, "rows": rows})
    assert len(text_) <= AGENT_MAX_TOOL_RESULT_CHARS
    data = json.loads(text_)["untrusted_data"]
    assert data["truncated"] is True and 0 < len(data["rows"]) < 200 and data["total"] == 200


def test_search_users_whitelist_and_masking(scripted_model):
    c = call("search_users", {"user_name": "admin"})
    body, _ = _admin_chat(scripted_model, tool_msg(c), final())
    row = _content(tool_results(body)[c["id"]])["untrusted_data"]["rows"][0]
    assert row["phonenumber"] == mask_phone("15888888888") == "158****8888"
    assert row["email"] == mask_email("ry@163.com") == "r***@163.com"
    assert "password" not in row and "delFlag" not in row and "createBy" not in row
    assert row["deptName"] == "研发部门"


def test_list_online_sessions_has_no_token_id(scripted_model):
    c = call("list_online_sessions")
    body, _ = _admin_chat(scripted_model, tool_msg(c), final())
    rows = _content(tool_results(body)[c["id"]])["untrusted_data"]["rows"]
    assert rows and all("tokenId" not in r for r in rows)
    assert any(r["userName"] == "admin" for r in rows)


def test_oper_logs_only_summary(scripted_model):
    # 先产生一条带参数的操作日志
    admin = auth_headers(client)
    client.put("/system/user/changeStatus", headers=admin, json={"userId": 2, "status": "0"})
    c = call("search_oper_logs", {"title": "用户管理"})
    # @log 用线程池异步落库，先等日志出现再让 Agent 查（直接轮询对话接口会触发频率限制）
    deadline = time.time() + 3
    while time.time() < deadline and not client.get("/monitor/operlog/list", headers=admin, params={"title": "用户管理"}).json()["total"]:
        time.sleep(0.1)
    scripted_model(tool_msg(c), final())
    body = chat(client, admin, "查操作日志")
    rows = _content(tool_results(body)[c["id"]])["untrusted_data"]["rows"]
    assert rows
    assert all("operParam" not in r and "jsonResult" not in r for r in rows)


def test_top_failed_logins_aggregates_in_sql(scripted_model):
    db = SessionLocal()
    now = datetime.now()
    data = [("tf_alice", "1", now)] * 4 + [("tf_bob", "1", now)] * 2 + [("tf_alice", "0", now)] * 3 \
        + [("tf_carol", "1", now - timedelta(days=20))] * 9
    db.execute(
        text("INSERT INTO sys_logininfor (user_name, ipaddr, status, msg, login_time) VALUES (:u, '10.0.0.1', :s, 'x', :t)"),
        [{"u": u, "s": s, "t": t} for u, s, t in data],
    )
    db.commit()
    try:
        c = call("top_failed_logins", {"days": 7, "limit": 20})
        body, _ = _admin_chat(scripted_model, tool_msg(c), final())
        rows = {r["userName"]: r["failCount"] for r in _content(tool_results(body)[c["id"]])["untrusted_data"]["rows"]}
        assert rows["tf_alice"] == 4 and rows["tf_bob"] == 2  # 成功登录不计入
        assert "tf_carol" not in rows  # 超出时间范围
    finally:
        db.execute(text("DELETE FROM sys_logininfor WHERE user_name LIKE 'tf\\_%'"))
        db.commit()
        db.close()


def test_login_lock_status_is_read_only(scripted_model):
    redis_client.set(PWD_ERR_CNT_KEY_PREFIX + "ry", 5, ex=300)
    c = call("get_login_lock_status", {"user_name": "ry"})
    body, _ = _admin_chat(scripted_model, tool_msg(c), final())
    data = _content(tool_results(body)[c["id"]])["untrusted_data"]
    assert data["locked"] is True and data["errorCount"] == 5 and 0 < data["remainSeconds"] <= 300
    assert redis_client.get(PWD_ERR_CNT_KEY_PREFIX + "ry") == "5"


def test_trim_history_keeps_current_turn_and_starts_on_human():
    old = []
    for i in range(40):
        old += [HumanMessage(content=f"旧问题{i}" + "x" * 1000), AIMessage(content="旧回答")]
    current = [
        HumanMessage(content="当前问题"),
        AIMessage(content="", tool_calls=[{"name": "list_roles", "args": {}, "id": "c1", "type": "tool_call"}]),
        ToolMessage(content="y" * 30000, tool_call_id="c1"),
    ]
    trimmed = policy.trim_history(old + current)
    assert trimmed[-3:] == current  # 当前轮完整保留，即使超过字符预算
    assert trimmed[0].type == "human"

    short = policy.trim_history(old[-6:] + current[:1])
    assert short[0].type == "human" and short[-1] is current[0] and len(short) == 7
