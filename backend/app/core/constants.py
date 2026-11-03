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

# 日期时间格式：返回给前端的时间字段、前端传来的日期范围参数都用这两种格式
DATE_FORMAT = "%Y-%m-%d"
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
# 日期范围只传日期时补全的时分秒：开始取当天最早、结束取当天最晚，结束日期当天才能整天查到
DAY_BEGIN_TIME = "00:00:00"
DAY_END_TIME = "23:59:59"

# 删除标志：存在/已删除
DEL_FLAG_NORMAL = "0"
DEL_FLAG_DELETED = "2"

# 菜单类型：目录/菜单/按钮
MENU_TYPE_DIR = "M"
MENU_TYPE_MENU = "C"
MENU_TYPE_BUTTON = "F"
MENU_ROOT_ID = 0  # 菜单根节点的 parent_id

# 菜单是否外链（0是 1否）
YES_FRAME = 0
NO_FRAME = 1

# ---------- 登录认证相关（对应若依 CacheConstants / Constants）----------

# Redis key 前缀
LOGIN_TOKEN_KEY_PREFIX = "login_tokens:"
CAPTCHA_CODE_KEY_PREFIX = "captcha_codes:"
PWD_ERR_CNT_KEY_PREFIX = "pwd_err_cnt:"
# SCAN 每批建议返回的 key 数量（只是提示值，Redis 不保证）
ONLINE_SCAN_COUNT = 100

# 验证码有效期（分钟）
CAPTCHA_EXPIRE_MINUTES = 2

# 登录态在 Redis 的有效期（分钟），对应 token.expireTime
LOGIN_TOKEN_EXPIRE_MINUTES = 30
# 剩余有效期不足该分钟数时自动续期
TOKEN_REFRESH_THRESHOLD_MINUTES = 20

# 密码错误锁定：最大重试次数 / 锁定时间（分钟）
PASSWORD_MAX_RETRY_COUNT = 5
PASSWORD_LOCK_MINUTES = 10

# 登录日志状态：0成功 1失败（login_service 里的 LOGIN_SUCCESS/LOGIN_FAIL 同值）
LOGIN_STATUS_SUCCESS = "0"
LOGIN_STATUS_FAIL = "1"

# Authorization 头的 token 前缀
TOKEN_PREFIX = "Bearer "
# JWT claims 里存 token uuid 的字段名
LOGIN_USER_KEY = "login_user_key"

# ---------- 操作日志业务类型（对应若依 BusinessType 枚举的 ordinal）----------
BIZ_OTHER = 0
BIZ_INSERT = 1
BIZ_UPDATE = 2
BIZ_DELETE = 3
BIZ_GRANT = 4
BIZ_EXPORT = 5
BIZ_IMPORT = 6
BIZ_FORCE = 7
BIZ_GENCODE = 8
BIZ_CLEAN = 9

# 操作日志状态：0正常 1异常
OPER_STATUS_SUCCESS = 0
OPER_STATUS_FAIL = 1

# has_perm 写入登录态副本的"当前接口权限字符"，对应 PermissionContextHolder
PERMISSION_CONTEXT_KEY = "permission_context"

# ---------- 智能助手（Agent）----------
AGENT_CHAT_PERM = "agent:chat:use"

# Redis key：每用户单运行锁、请求频率限制
AGENT_RUN_LOCK_KEY_PREFIX = "agent_run_lock:"
AGENT_RATE_LIMIT_KEY_PREFIX = "agent_rate_limit:"
# 单次运行（一次对话或一次审批恢复）的总时限（秒）：每次调用模型前检查剩余时间，
# 剩余时间不足一次模型调用的超时就停止，保证整次运行一定在时限内结束
AGENT_RUN_DEADLINE_SECONDS = 180
# 运行锁的过期时间（秒）：兜底防止进程崩溃后锁永远不释放。必须大于"运行时限 + 一次模型调用超时"，
# 否则锁可能在运行还没结束时过期，同一用户就能并发跑第二个运行（测试里有断言）
AGENT_RUN_LOCK_SECONDS = 300
# 频率限制：每个窗口（秒）内每用户最多请求次数
AGENT_RATE_LIMIT_WINDOW_SECONDS = 60
AGENT_RATE_LIMIT_MAX_REQUESTS = 20

# 图执行上限：防止模型陷入"调用工具 -> 再调用工具"的死循环，或一次塞入大量工具调用
AGENT_RECURSION_LIMIT = 25
AGENT_MAX_MODEL_STEPS = 8
AGENT_MAX_TOOL_CALLS_PER_STEP = 5
# 单个工具结果（JSON 字符串）的最大字符数，超出截断，避免撑爆上下文
AGENT_MAX_TOOL_RESULT_CHARS = 4000
# 工具分页上限：模型传再大的 pageSize 也只给这么多
AGENT_MAX_PAGE_SIZE = 20
# 发送给模型的历史消息裁剪：保留最近 N 条，且总字符数不超过上限
AGENT_MAX_HISTORY_MESSAGES = 30
AGENT_MAX_HISTORY_CHARS = 24000
# 用户单条输入长度上限
AGENT_MAX_INPUT_CHARS = 2000
# 会话标题取首条消息的前若干字
AGENT_TITLE_CHARS = 30
# 模型调用超时（秒）；不自动重试，所以这也是单次模型调用的最长耗时
AGENT_MODEL_TIMEOUT_SECONDS = 30

# 写操作审批（agent_action）
AGENT_ACTION_EXPIRE_MINUTES = 10
ACTION_PENDING = "PENDING"
ACTION_APPROVED = "APPROVED"
ACTION_REJECTED = "REJECTED"
ACTION_EXPIRED = "EXPIRED"
ACTION_EXECUTING = "EXECUTING"
ACTION_SUCCEEDED = "SUCCEEDED"
ACTION_FAILED = "FAILED"
# 审批决定（Command(resume=...) 的值）
DECISION_APPROVE = "approve"
DECISION_REJECT = "reject"
DECISION_EXPIRE = "expire"
# 操作日志里标记"来源是智能助手"：写在 sys_oper_log.method 字段前缀
AGENT_OPER_LOG_SOURCE = "智能助手"
