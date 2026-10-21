"""
数据权限：根据登录用户的角色拼接 SQL 过滤条件，对应若依的 DataScopeAspect + @DataScope 注解。

规则（见 TASK.md 阶段3）：
  1 全部：不过滤（命中即停止，不再看其他角色）
  2 自定义：dept_id IN (SELECT dept_id FROM sys_role_dept WHERE role_id = ?)
  3 本部门：dept_id = 用户部门
  4 本部门及以下：dept_id IN (SELECT dept_id FROM sys_dept WHERE dept_id = ? OR FIND_IN_SET(?, ancestors))
  5 仅本人：user_id = 当前用户
多个角色的条件之间用 OR 合并；超级管理员不过滤。
"""
from app.core.constants import SUPER_ADMIN_ID


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

    conditions: list[str] = []
    dept_id = int(login_user.get("dept_id") or 0)
    user_id = int(login_user["user_id"])

    for role in login_user.get("roles", []):
        scope = role.get("data_scope")
        role_id = int(role["role_id"])
        if scope == "1":
            return None
        elif scope == "2":
            conditions.append(f"{dept_alias}.dept_id IN (SELECT dept_id FROM sys_role_dept WHERE role_id = {role_id})")
        elif scope == "3":
            conditions.append(f"{dept_alias}.dept_id = {dept_id}")
        elif scope == "4":
            conditions.append(
                f"{dept_alias}.dept_id IN (SELECT dept_id FROM sys_dept "
                f"WHERE dept_id = {dept_id} OR FIND_IN_SET({dept_id}, ancestors))"
            )
        elif scope == "5":
            if user_alias:
                conditions.append(f"{user_alias}.user_id = {user_id}")
            else:
                conditions.append(f"{dept_alias}.dept_id = 0")

    if not conditions:
        # 没有任何角色（理论上不会发生），保守起见什么都不给看
        return f"{dept_alias}.dept_id = 0"
    return "(" + " OR ".join(conditions) + ")"
