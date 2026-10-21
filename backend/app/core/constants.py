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

# ---------- 登录认证相关（对应若依 CacheConstants / Constants）----------

# Redis key 前缀
LOGIN_TOKEN_KEY_PREFIX = "login_tokens:"
CAPTCHA_CODE_KEY_PREFIX = "captcha_codes:"
PWD_ERR_CNT_KEY_PREFIX = "pwd_err_cnt:"

# 验证码有效期（分钟）
CAPTCHA_EXPIRE_MINUTES = 2

# 登录态在 Redis 的有效期（分钟），对应 token.expireTime
LOGIN_TOKEN_EXPIRE_MINUTES = 30
# 剩余有效期不足该分钟数时自动续期
TOKEN_REFRESH_THRESHOLD_MINUTES = 20

# 密码错误锁定：最大重试次数 / 锁定时间（分钟）
PASSWORD_MAX_RETRY_COUNT = 5
PASSWORD_LOCK_MINUTES = 10

# Authorization 头的 token 前缀
TOKEN_PREFIX = "Bearer "
# JWT claims 里存 token uuid 的字段名
LOGIN_USER_KEY = "login_user_key"
