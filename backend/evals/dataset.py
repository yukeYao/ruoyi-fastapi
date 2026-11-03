"""加载并校验固定数据集：用例写错（未知检查类型、未知操作者）时在运行前就报错，避免跑完才发现结果无效"""
import json
from pathlib import Path

DATASET_FILE = Path(__file__).resolve().parent / "dataset.json"

CATEGORIES = ("query", "write", "security", "injection")
OPERATORS = ("admin", "ev_op_scope", "ev_op_readonly")
CHECK_TYPES = ("reply_contains", "reply_not_contains", "reply_not_executed", "user_status", "sessions", "unlocked",
               "no_write_attempt")
CHECK_KINDS = ("task", "constraint")
FOLLOWUPS = ("confirm", "cancel")
WRITE_TOOLS = ("change_user_status", "unlock_user_account", "force_logout_user")
READ_TOOLS = ("search_users", "get_user_detail", "list_roles", "list_departments", "search_login_logs",
              "top_failed_logins", "get_login_lock_status", "search_oper_logs", "list_online_sessions")


def validate(cases: list[dict]) -> None:
    ids = [c["id"] for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("用例 id 重复")
    for c in cases:
        where = f"用例 {c.get('id')}"
        if c.get("category") not in CATEGORIES:
            raise ValueError(f"{where}：category 无效")
        if c.get("operator") not in OPERATORS:
            raise ValueError(f"{where}：operator 无效")
        if not c.get("turns") or not all(isinstance(t, str) and t for t in c["turns"]):
            raise ValueError(f"{where}：turns 不能为空")
        if c.get("followup") not in (None, *FOLLOWUPS):
            raise ValueError(f"{where}：followup 无效")
        for tool in [*c.get("expect_tools", []), *c.get("allow_tools", [])]:
            if tool not in (*READ_TOOLS, *WRITE_TOOLS):
                raise ValueError(f"{where}：未知工具 {tool}")
        for item in [*c.get("approve", []), *c.get("security", {}).get("forbidden", []),
                     *c.get("injection", {}).get("forbidden", [])]:
            if item.get("tool") not in WRITE_TOOLS or not item.get("user"):
                raise ValueError(f"{where}：写操作描述需要 tool 和 user")
        for check in c.get("checks", []):
            if check.get("type") not in CHECK_TYPES or check.get("kind") not in CHECK_KINDS:
                raise ValueError(f"{where}：检查项 {check} 无效")
        if c["category"] == "security" and "security" not in c:
            raise ValueError(f"{where}：安全用例需要 security 字段")
        if c["category"] == "injection" and "injection" not in c:
            raise ValueError(f"{where}：注入用例需要 injection 字段")


def dataset_version() -> int:
    return json.loads(DATASET_FILE.read_text(encoding="utf-8"))["version"]


def load_cases(ids: list[str] | None = None) -> list[dict]:
    cases = json.loads(DATASET_FILE.read_text(encoding="utf-8"))["cases"]
    validate(cases)
    if ids:
        unknown = set(ids) - {c["id"] for c in cases}
        if unknown:
            raise ValueError(f"未知用例：{sorted(unknown)}")
        cases = [c for c in cases if c["id"] in ids]
    return cases
