"""
数据权限：根据登录用户的角色拼接 SQL 过滤条件，对应若依的 DataScopeAspect.dataScopeFilter + @DataScope 注解。

规则（见 TASK.md 阶段3）：
  1 全部：不过滤（命中即停止，不再看其他角色）
  2 自定义：dept_id IN (SELECT dept_id FROM sys_role_dept WHERE role_id = ?)，多个自定义角色合并成 role_id IN (...)
  3 本部门：dept_id = 用户部门
  4 本部门及以下：dept_id IN (SELECT dept_id FROM sys_dept WHERE dept_id = ? OR FIND_IN_SET(?, ancestors))
  5 仅本人：user_id = 当前用户
多个角色的条件之间用 OR 合并；超级管理员不过滤。参与计算的角色要满足：
  - 角色状态正常（停用角色跳过）；
  - 当前接口有权限字符时（has_perm 写入的 PERMISSION_CONTEXT_KEY），角色自己的权限里要包含它，
    比如查用户列表只看拥有 system:user:list 的角色的数据范围；
  - 同一种数据范围只算一次。
没有任何角色参与时什么都看不到。
"""
from app.core.constants import PERMISSION_CONTEXT_KEY, STATUS_DISABLE, STATUS_NORMAL, SUPER_ADMIN_ID


def build_data_scope_filter(login_user: dict, dept_alias: str = "d", user_alias: str | None = "u") -> str | None:
    """
    返回一个布尔表达式字符串，用 `db.query(...).filter(text(expr))` 拼到查询里；
    返回 None 表示不需要过滤（超级管理员，或某个角色是"全部数据权限")。

    user_alias：查询里 sys_user 表的别名，用于"仅本人"这条规则。若查询里根本没有 join
    sys_user（比如部门列表、角色列表），按若依源码的真实行为传 None——"仅本人"这时退化成
    "{dept_alias}.dept_id = 0"（什么都看不到），而不是去引用一个不存在的表别名。
    """
    if login_user.get("user_id") == SUPER_ADMIN_ID:
        return None

    # 对应 Convert.toStrArray(permission)：权限字符可以是逗号分隔的多个
    context_perms = [p for p in (login_user.get(PERMISSION_CONTEXT_KEY) or "").split(",") if p]

    def has_context_perm(role: dict) -> bool:
        return any(p in role.get("permissions", []) for p in context_perms)

    roles = login_user.get("roles", [])
    dept_id = int(login_user.get("dept_id") or 0)
    user_id = int(login_user["user_id"])

    custom_role_ids = [
        int(r["role_id"])
        for r in roles
        if r.get("data_scope") == "2" and r.get("status") == STATUS_NORMAL and (not context_perms or has_context_perm(r))
    ]

    conditions: list[str] = []  # 已处理过的数据范围
    sql_parts: list[str] = []
    for role in roles:
        scope = role.get("data_scope")
        if scope in conditions or role.get("status") == STATUS_DISABLE:
            continue
        if context_perms and not has_context_perm(role):
            continue
        if scope == "1":
            return None
        elif scope == "2":
            if len(custom_role_ids) > 1:
                ids = ",".join(str(i) for i in custom_role_ids)
                sql_parts.append(f"{dept_alias}.dept_id IN (SELECT dept_id FROM sys_role_dept WHERE role_id IN ({ids}))")
            else:
                sql_parts.append(f"{dept_alias}.dept_id IN (SELECT dept_id FROM sys_role_dept WHERE role_id = {int(role['role_id'])})")
        elif scope == "3":
            sql_parts.append(f"{dept_alias}.dept_id = {dept_id}")
        elif scope == "4":
            sql_parts.append(
                f"{dept_alias}.dept_id IN (SELECT dept_id FROM sys_dept "
                f"WHERE dept_id = {dept_id} OR FIND_IN_SET({dept_id}, ancestors))"
            )
        elif scope == "5":
            if user_alias:
                sql_parts.append(f"{user_alias}.user_id = {user_id}")
            else:
                sql_parts.append(f"{dept_alias}.dept_id = 0")
        conditions.append(scope)

    # 角色都不包含当前接口的权限字符（或都已停用）时什么都不给看
    if not conditions:
        sql_parts.append(f"{dept_alias}.dept_id = 0")
    return "(" + " OR ".join(sql_parts) + ")" if sql_parts else None
