"""
全局常量，对应若依 Java 版 `com.ruoyi.common.constant.UserConstants` 等。
"""

# 超级管理员 userId / roleId，若依里硬编码为 1，不可删除/停用/修改
SUPER_ADMIN_ID = 1
SUPER_ADMIN_ROLE_ID = 1

# 超级管理员拥有的权限标识，匹配一切权限
ALL_PERMISSION = "*:*:*"
ADMIN_ROLE_KEY = "admin"

# 正常/停用状态
STATUS_NORMAL = "0"
STATUS_DISABLE = "1"

# 删除标志：存在/已删除
DEL_FLAG_NORMAL = "0"
DEL_FLAG_DELETED = "2"

# 菜单类型：目录/菜单/按钮
MENU_TYPE_DIR = "M"
MENU_TYPE_MENU = "C"
MENU_TYPE_BUTTON = "F"

# 菜单是否外链（0是 1否）
YES_FRAME = 0
NO_FRAME = 1
