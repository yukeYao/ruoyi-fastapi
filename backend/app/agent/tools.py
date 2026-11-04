"""
工具注册表：每个工具声明 名称、风险级别、权限字符、入参模型、执行函数、输出字段白名单。

约束：
- 只有白名单业务工具，没有执行任意 SQL / HTTP / 代码的工具；
- 执行函数一律调用现有 service，复用其中的数据权限和业务校验，不另写 SQL；
- 执行函数收到的 login_user 已经由 policy 层 check_perm 处理过（带 PERMISSION_CONTEXT_KEY），
  否则 build_data_scope_filter 会让所有角色参与计算，数据范围变大；
- 入参模型 extra="forbid" 且限制取值范围：模型传来的参数一律当作不可信输入。
"""
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.agent.serialization import MASK_EMAIL, MASK_PHONE
from app.agent import write_tools
from app.core.constants import (
    AGENT_CHAT_PERM,
    AGENT_MAX_PAGE_SIZE,
    ALL_PERMISSION,
    BIZ_FORCE,
    BIZ_OTHER,
    BIZ_UPDATE,
    KNOWLEDGE_MAX_QUERY_CHARS,
    KNOWLEDGE_MAX_TOP_K,
    KNOWLEDGE_TOP_K,
    STATUS_DISABLE,
    SUPER_ADMIN_ROLE_ID,
)
from app.core.exceptions import ServiceException
from app.rag.documents import PERM_PATTERN
from app.services import (
    dept_service,
    knowledge_service,
    logininfor_service,
    menu_service,
    online_service,
    operlog_service,
    role_service,
    user_service,
)

RISK_READ = "read"
RISK_WRITE = "write"

# 列表类工具不分页（部门、在线会话）时最多返回的条数
_MAX_LIST_ROWS = 50
# 日志类工具最多回看的天数
_MAX_DAYS = 30


@dataclass(frozen=True)
class ToolOutput:
    """
    只读工具需要附带服务端说明时的返回值：data 照常经过白名单、放进 untrusted_data；
    note 是服务端生成的固定文案，放在工具结果外层（可信），例如"知识库没有找到依据""列表已截断"
    """
    data: dict
    note: str = ""


class ToolArgs(BaseModel):
    # forbid：模型多传字段直接判为非法参数，而不是悄悄忽略
    model_config = ConfigDict(extra="forbid")


class PageArgs(ToolArgs):
    page_num: int = Field(default=1, ge=1, le=1000, description="页码，从 1 开始")
    page_size: int = Field(default=10, ge=1, le=AGENT_MAX_PAGE_SIZE, description=f"每页条数，最多 {AGENT_MAX_PAGE_SIZE}")


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    risk: str
    perm: str
    args_model: type[ToolArgs]
    # 只读工具：(db, 已带权限上下文的 login_user, 已校验的参数) -> {"rows": [...], 其他标量} 或单条 dict
    run: Callable[[Session, dict, Any], dict] | None = None
    # 输出字段白名单：policy 层统一按它挑字段（列表结果作用于每一行），白名单外的字段一律丢弃
    output_fields: tuple[str, ...] = ()
    # 需要脱敏的字段：{字段名: MASK_PHONE / MASK_EMAIL}
    masks: dict[str, str] = field(default_factory=dict)
    # 结果受数据权限过滤（用户、角色、部门）：为空时 policy 附带"不存在或不在数据范围内"的说明
    data_scoped: bool = False
    # 写工具：preview(db, 已带权限上下文的 login_user, 已校验的参数, 操作者会话 token) -> 预览 dict，
    # 只读，预览里的 target.userId / userName 由 propose 冻结；
    # execute(..., 冻结的目标 {userId, userName}) -> 结果 dict，只按冻结的目标操作
    preview: Callable[[Session, dict, Any, str | None], dict] | None = None
    execute: Callable[[Session, dict, Any, str | None, dict], dict] | None = None
    # 写入 sys_oper_log 的模块标题和业务类型，与对应的现有路由 @log 保持一致
    oper_title: str = ""
    business_type: int = BIZ_OTHER


def _since(days: int) -> datetime:
    return datetime.now() - timedelta(days=days)


# ---------------------------------------------------------------------------
# 只读工具
# ---------------------------------------------------------------------------


class SearchUsersArgs(PageArgs):
    user_name: str | None = Field(default=None, max_length=30, description="用户名，模糊匹配")
    status: Literal["0", "1"] | None = Field(default=None, description="0 正常，1 停用")
    dept_id: int | None = Field(default=None, ge=1, description="部门ID，包含下级部门")


def _search_users(db: Session, login_user: dict, args: SearchUsersArgs) -> dict:
    rows, total = user_service.list_users(
        db, login_user, args.user_name, args.status, None, args.dept_id, None, None, args.page_num, args.page_size
    )
    return {"total": total, "rows": rows}


class UserIdArgs(ToolArgs):
    user_id: int = Field(ge=1, description="用户ID")


def _get_user_detail(db: Session, login_user: dict, args: UserIdArgs) -> dict:
    # get_user_detail 内部先做 check_user_data_scope，范围外用户直接抛"没有权限访问用户数据！"
    detail = user_service.get_user_detail(db, login_user, args.user_id)
    role_names = {r["roleId"]: r["roleName"] for r in detail.get("roles", [])}
    roles = [{"roleId": rid, "roleName": role_names.get(rid, "")} for rid in detail.get("roleIds", [])]
    return {**detail["data"], "roles": roles}


class ListRolesArgs(PageArgs):
    role_name: str | None = Field(default=None, max_length=30, description="角色名称，模糊匹配")
    status: Literal["0", "1"] | None = Field(default=None, description="0 正常，1 停用")


def _list_roles(db: Session, login_user: dict, args: ListRolesArgs) -> dict:
    rows, total = role_service.list_roles(db, login_user, args.role_name, args.status, None, None, None, args.page_num, args.page_size)
    return {"total": total, "rows": rows}


class ListDeptsArgs(ToolArgs):
    dept_name: str | None = Field(default=None, max_length=30, description="部门名称，模糊匹配")
    status: Literal["0", "1"] | None = Field(default=None, description="0 正常，1 停用")


def _list_departments(db: Session, login_user: dict, args: ListDeptsArgs) -> dict:
    rows = dept_service.list_depts(db, login_user, args.dept_name, args.status)
    return {"total": len(rows), "rows": rows[:_MAX_LIST_ROWS]}


class SearchLoginLogsArgs(PageArgs):
    user_name: str | None = Field(default=None, max_length=50, description="用户名，模糊匹配")
    ipaddr: str | None = Field(default=None, max_length=128, description="登录IP，模糊匹配")
    status: Literal["0", "1"] | None = Field(default=None, description="0 成功，1 失败")
    days: int = Field(default=7, ge=1, le=_MAX_DAYS, description="查询最近多少天")


def _search_login_logs(db: Session, login_user: dict, args: SearchLoginLogsArgs) -> dict:
    rows, total = logininfor_service.list_logininfor(
        db, args.ipaddr, args.user_name, args.status, _since(args.days), None, args.page_num, args.page_size
    )
    return {"total": total, "rows": rows}


class TopFailedLoginsArgs(ToolArgs):
    days: int = Field(default=7, ge=1, le=_MAX_DAYS, description="统计最近多少天")
    limit: int = Field(default=10, ge=1, le=AGENT_MAX_PAGE_SIZE, description="返回前几名")


def _top_failed_logins(db: Session, login_user: dict, args: TopFailedLoginsArgs) -> dict:
    rows = logininfor_service.top_failed_logins(db, _since(args.days), args.limit)
    return {"days": args.days, "rows": rows}


class UserNameArgs(ToolArgs):
    user_name: str = Field(min_length=1, max_length=30, description="用户名（精确）")


def _get_login_lock_status(db: Session, login_user: dict, args: UserNameArgs) -> dict:
    return logininfor_service.get_lock_status(args.user_name)


class SearchOperLogsArgs(PageArgs):
    title: str | None = Field(default=None, max_length=50, description="模块标题，模糊匹配")
    oper_name: str | None = Field(default=None, max_length=50, description="操作人，模糊匹配")
    business_type: int | None = Field(default=None, ge=0, le=9, description="业务类型：0其它 1新增 2修改 3删除 ...")
    status: Literal[0, 1] | None = Field(default=None, description="0 正常，1 异常")
    days: int = Field(default=7, ge=1, le=_MAX_DAYS, description="查询最近多少天")


def _search_oper_logs(db: Session, login_user: dict, args: SearchOperLogsArgs) -> dict:
    rows, total = operlog_service.list_oper_logs(
        db, args.title, args.business_type, args.status, args.oper_name, _since(args.days), None, args.page_num, args.page_size
    )
    for r in rows:
        r["errorMsg"] = (r.get("errorMsg") or "")[:200]
    return {"total": total, "rows": rows}


class ListOnlineArgs(ToolArgs):
    user_name: str | None = Field(default=None, max_length=30, description="用户名（精确）")
    ipaddr: str | None = Field(default=None, max_length=128, description="登录IP（精确）")


def _list_online_sessions(db: Session, login_user: dict, args: ListOnlineArgs) -> dict:
    rows = online_service.list_online_users(args.ipaddr, args.user_name)
    return {"total": len(rows), "rows": rows[:_MAX_LIST_ROWS]}


class SearchKnowledgeArgs(ToolArgs):
    query: str = Field(min_length=1, max_length=KNOWLEDGE_MAX_QUERY_CHARS,
                       description=f"要查询的系统规则、权限配置或操作步骤问题，最多 {KNOWLEDGE_MAX_QUERY_CHARS} 字")
    top_k: int = Field(default=KNOWLEDGE_TOP_K, ge=1, le=KNOWLEDGE_MAX_TOP_K, description="返回的片段数")


def _search_knowledge(db: Session, login_user: dict, args: SearchKnowledgeArgs) -> ToolOutput:
    # 权限过滤、降级、无结果说明都在 knowledge_service 里完成；这里只换成工具结果的字段名
    result = knowledge_service.search_knowledge(db, login_user, args.query, args.top_k)
    rows = [{"docId": h.doc_id, "title": h.title, "section": h.section, "chunkId": h.chunk_id, "snippet": h.snippet,
             "score": h.score} for h in result.hits]
    return ToolOutput({"total": len(rows), "rows": rows}, note=result.note)


# 角色的权限字符最多列出这么多个；超出时标记 truncated，并在外层说明列表不完整
MAX_ROLE_PERMS = 60
MSG_ROLE_UNAVAILABLE = "该角色不存在或不在你的数据范围内"
NOTE_ROLE_SESSION = ("以上是数据库中的角色配置，不等于用户当前登录会话里的权限：角色、菜单授权或角色状态变更后，"
                     "已登录的用户通常要重新登录才会更新，不能据此断言用户当前会话已经生效。")
NOTE_ROLE_DISABLED = "该角色已停用：停用的角色不会给用户带来任何权限。"
NOTE_ROLE_TRUNCATED = ("权限字符共 {total} 个，这里只列出按字母排序的前 {shown} 个，列表不完整："
                       "不能据此断言该角色没有某个权限，需要确认某个权限时用 perm 参数查询。")


class GetRolePermissionsArgs(ToolArgs):
    role_name: str = Field(min_length=1, max_length=30, description="角色名称（精确匹配），可先用 get_user_detail 或 list_roles 查到")
    perm: str | None = Field(default=None, max_length=100, pattern=PERM_PATTERN.pattern,
                             description="可选：要确认的权限字符，例如 agent:chat:use；给出时返回该角色是否拥有它")


def _get_role_permissions(db: Session, login_user: dict, args: GetRolePermissionsArgs) -> ToolOutput:
    role = role_service.get_role_by_name(db, args.role_name)
    if role is None:
        raise ServiceException(MSG_ROLE_UNAVAILABLE)
    try:
        # 数据范围按当前工具的权限字符（system:role:query）计算；范围外与不存在返回同一句话
        role_service.check_role_data_scope(db, login_user, role.role_id)
    except ServiceException:
        raise ServiceException(MSG_ROLE_UNAVAILABLE) from None
    perms = [ALL_PERMISSION] if role.role_id == SUPER_ADMIN_ROLE_ID else menu_service.list_role_perms(db, role.role_id)
    data = {"roleId": role.role_id, "roleName": role.role_name, "roleKey": role.role_key, "status": role.status,
            "dataScope": role.data_scope, "permCount": len(perms), "perms": perms[:MAX_ROLE_PERMS],
            "truncated": len(perms) > MAX_ROLE_PERMS}
    if args.perm:
        # 完整列表上判断，不受截断影响
        data["checkedPerm"] = args.perm
        data["hasPerm"] = args.perm in perms or ALL_PERMISSION in perms
    notes = [NOTE_ROLE_SESSION]
    if role.status == STATUS_DISABLE:
        notes.append(NOTE_ROLE_DISABLED)
    if data["truncated"]:
        notes.append(NOTE_ROLE_TRUNCATED.format(total=len(perms), shown=MAX_ROLE_PERMS))
    return ToolOutput(data, note="".join(notes))


# ---------------------------------------------------------------------------
# 写工具：模型调用后只生成 PENDING 审批（预览见 write_tools.py），用户批准后服务端才执行
# ---------------------------------------------------------------------------

class TargetUserArgs(ToolArgs):
    """
    写工具的目标只接受用户名（登录账号），不接受 user_id：由服务端精确解析成唯一用户并冻结，
    模型猜错 ID 就可能操作到别人（冒烟测试里真实模型就猜过 user_id=1）。
    extra="forbid" 保证模型无法额外提交 user_id / target_user_id。
    """
    user_name: str = Field(min_length=1, max_length=30, description="目标用户的登录账号（精确匹配）；只知道姓名或条件时先用 search_users 查询")


class ChangeUserStatusArgs(TargetUserArgs):
    status: Literal["0", "1"] = Field(description="0 启用，1 停用")


_USER_MASKS = {"phonenumber": MASK_PHONE, "email": MASK_EMAIL}

_TOOL_LIST = [
    ToolSpec("search_users", "按条件分页查询用户（受数据权限限制），手机号和邮箱已脱敏", RISK_READ, "system:user:list",
             SearchUsersArgs, _search_users,
             ("userId", "userName", "nickName", "deptId", "dept.deptName", "status", "phonenumber", "email",
              "loginIp", "loginDate", "createTime"), _USER_MASKS, data_scoped=True),
    ToolSpec("get_user_detail", "查询单个用户详情及其角色（受数据权限限制）", RISK_READ, "system:user:query",
             UserIdArgs, _get_user_detail,
             ("userId", "userName", "nickName", "deptId", "status", "phonenumber", "email", "sex",
              "loginIp", "loginDate", "createTime", "remark", "roles"), _USER_MASKS),
    ToolSpec("list_roles", "分页查询角色", RISK_READ, "system:role:list", ListRolesArgs, _list_roles,
             ("roleId", "roleName", "roleKey", "roleSort", "dataScope", "status", "createTime"), data_scoped=True),
    ToolSpec("list_departments", "查询部门列表", RISK_READ, "system:dept:list", ListDeptsArgs, _list_departments,
             ("deptId", "parentId", "deptName", "leader", "status", "orderNum"), data_scoped=True),
    ToolSpec("search_login_logs", "分页查询最近若干天的登录日志", RISK_READ, "monitor:logininfor:list",
             SearchLoginLogsArgs, _search_login_logs,
             ("infoId", "userName", "ipaddr", "loginLocation", "browser", "os", "status", "msg", "loginTime")),
    ToolSpec("top_failed_logins", "统计最近若干天登录失败次数最多的账号", RISK_READ, "monitor:logininfor:list",
             TopFailedLoginsArgs, _top_failed_logins, ("userName", "failCount", "ipCount", "lastFailTime")),
    ToolSpec("get_login_lock_status", "查询账号的密码错误次数和锁定剩余时间", RISK_READ, "monitor:logininfor:list",
             UserNameArgs, _get_login_lock_status, ("userName", "errorCount", "locked", "remainSeconds")),
    # 只给摘要：oper_param / json_result 里可能有用户提交的任意内容和敏感数据，不在白名单里
    ToolSpec("search_oper_logs", "分页查询最近若干天的操作日志摘要", RISK_READ, "monitor:operlog:list",
             SearchOperLogsArgs, _search_oper_logs,
             ("operId", "title", "businessType", "operName", "deptName", "requestMethod", "operUrl", "operIp",
              "status", "errorMsg", "operTime", "costTime")),
    # 不返回 tokenId：拿到它就能强制下线任意会话；强制下线工具改为按用户名在服务端解析会话
    ToolSpec("list_online_sessions", "查询在线会话", RISK_READ, "monitor:online:list", ListOnlineArgs, _list_online_sessions,
             ("userName", "deptName", "ipaddr", "loginLocation", "browser", "os", "loginTime")),
    # 知识库只读：检索内容是文档数据，放在 untrusted_data；降级、无结果的说明是服务端外层 note
    ToolSpec("search_knowledge", "检索知识库中的系统规则、权限配置、操作步骤和常见问题（只说明规则，不代表实时数据）",
             RISK_READ, AGENT_CHAT_PERM, SearchKnowledgeArgs, _search_knowledge,
             ("docId", "title", "section", "chunkId", "snippet", "score")),
    ToolSpec("get_role_permissions", "按角色名称查询角色状态、数据范围和已授权的权限字符（数据库配置，受数据权限限制）",
             RISK_READ, "system:role:query", GetRolePermissionsArgs, _get_role_permissions,
             ("roleId", "roleName", "roleKey", "status", "dataScope", "permCount", "perms", "truncated",
              "checkedPerm", "hasPerm")),
    ToolSpec("change_user_status", "按登录账号启用或停用用户。只会提交审批，用户批准后才执行；停用不会让已登录会话下线",
             RISK_WRITE, "system:user:edit", ChangeUserStatusArgs,
             preview=write_tools.preview_change_user_status, execute=write_tools.execute_change_user_status,
             oper_title="用户管理", business_type=BIZ_UPDATE),
    ToolSpec("unlock_user_account", "按登录账号清除密码错误次数（解除锁定）。只会提交审批，用户批准后才执行",
             RISK_WRITE, "monitor:logininfor:unlock", TargetUserArgs,
             preview=write_tools.preview_unlock_user_account, execute=write_tools.execute_unlock_user_account,
             oper_title="账户解锁", business_type=BIZ_OTHER),
    ToolSpec("force_logout_user", "按登录账号强制下线该用户的全部在线会话。只会提交审批，用户批准后才执行；不会停用账号",
             RISK_WRITE, "monitor:online:forceLogout", TargetUserArgs,
             preview=write_tools.preview_force_logout_user, execute=write_tools.execute_force_logout_user,
             oper_title="在线用户", business_type=BIZ_FORCE),
]

TOOLS: dict[str, ToolSpec] = {spec.name: spec for spec in _TOOL_LIST}

# 能力的中文名：用于服务端生成的"能力边界"和无权限提示
TOOL_TITLES = {
    "search_users": "查询用户",
    "get_user_detail": "查看用户详情",
    "list_roles": "查询角色",
    "list_departments": "查询部门",
    "search_login_logs": "查询登录日志",
    "top_failed_logins": "统计登录失败",
    "get_login_lock_status": "查询账号锁定状态",
    "search_oper_logs": "查询操作日志",
    "list_online_sessions": "查询在线会话",
    "search_knowledge": "检索知识库",
    "get_role_permissions": "查询角色权限",
    "change_user_status": "启用/停用用户账号",
    "unlock_user_account": "解除账号锁定",
    "force_logout_user": "强制下线",
}


def tool_schema(spec: ToolSpec) -> dict:
    """转换成 OpenAI function calling 格式，供 bind_tools 使用"""
    return {
        "type": "function",
        "function": {"name": spec.name, "description": spec.description, "parameters": spec.args_model.model_json_schema()},
    }
