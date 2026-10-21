# 任务：用 FastAPI 复现若依（RuoYi）后端 —— 精简学习版

> 这份文件是给 Claude Code 的完整任务说明。请先通读全文，再按「阶段」顺序执行。

---

## 0. 工作方式（请先读）

- **用中文**与我交流，代码注释也用中文，并且注释要解释"为什么这么做"，因为这个项目是我用来学习原理、准备校招面试的。
- 工作目录就是当前目录（`D:\0Programs\0internship\ruoyi`）。系统是 **Windows**，命令请用 PowerShell / cmd 可执行的写法。
- **唯一标准**：前端以 RuoYi-Vue3 源码（`src/api/**/*.js`、`src/store`、`src/utils/request.js`、`src/permission.js`）为准；后端行为以 RuoYi-Vue 的 Java 源码为准。本文给出的接口和逻辑是摘要，**如与源码冲突，以源码为准**，并告诉我差异。
- 按阶段推进。每个阶段完成后：自测通过 → `git commit`（提交信息用中文写明完成了什么）→ 用两三句话告诉我完成了什么，然后继续下一阶段。
- 需要我提供信息时（例如 MySQL root 密码）**停下来问我**，不要猜。
- 不要做第 9 节「不做的范围」里的功能，保持项目精简、易懂。
- 代码风格：**同步写法**（普通 `def`，同步 SQLAlchemy，同步 redis-py），不要用 async。

---

## 1. 背景与目标

我是 Python 后端方向的校招生，想把这个项目写进简历。目标：

1. 用 **FastAPI** 重写若依后端的核心功能，**前端直接使用若依官方 RuoYi-Vue3，不改业务代码**；
2. 接口路径、参数、返回格式与若依 Java 版完全一致，前端能直接对接；
3. 代码分层清晰、注释充分，我能讲清每个模块的原理。

---

## 2. 我的环境（已确认可用）

| 项目 | 版本 / 说明 |
|---|---|
| 系统 | Windows |
| Python | 3.12.7（已加入 PATH） |
| Node.js | v24.22.0 |
| MySQL | 8.4.13，本地服务，端口 3306，用户 root（**密码开工前问我**），`mysql` 命令**不在 PATH 中** |
| Redis | Windows 版 5.0.14，端口 6379，无密码，`redis-cli ping` 正常 |
| 数据库工具 | Navicat Premium 17 |
| git | 不确定是否安装，请先检查 |

注意：

- MySQL 8.4 默认认证方式是 `caching_sha2_password`，用 PyMySQL 连接时**必须安装 `cryptography`**。
- 国内网络：pip 使用清华镜像 `-i https://pypi.tuna.tsinghua.edu.cn/simple`；npm 使用 `--registry=https://registry.npmmirror.com`。

---

## 3. 阶段 0：准备

1. 检查 `git --version`。若没有 git，告诉我，并改为从网页下载 zip 的方式获取下面两个仓库。
2. 在当前目录下创建 `reference/` 目录，获取两个参考仓库（**优先 Gitee，速度快**；失败再用 GitHub）：
   - 后端（取 Java 源码和 SQL）：`https://gitee.com/y_project/RuoYi-Vue.git`（GitHub：`yangzongzhuan/RuoYi-Vue`）
   - 前端：`https://gitee.com/y_project/RuoYi-Vue3.git`（GitHub：`yangzongzhuan/RuoYi-Vue3`）
   - 用 `--depth 1` 浅克隆即可。`reference/` 加入 `.gitignore`。
3. 前端单独复制一份到 `ruoyi-ui/`（我实际运行用这份），`reference/` 只用来读源码。
4. 问我 MySQL root 密码，写入 `backend/.env`（`.env` 加入 `.gitignore`，另提供 `.env.example`）。
5. 创建虚拟环境 `backend/.venv`，安装依赖。
6. `git init`，提交初始结构。

---

## 4. 技术栈与目录结构

**依赖**（写入 `backend/requirements.txt`）：
`fastapi`、`uvicorn[standard]`、`sqlalchemy>=2.0`、`pymysql`、`cryptography`、`redis`、`pyjwt`、`bcrypt`、`pillow`（验证码）、`pydantic-settings`、`user-agents`（解析浏览器/系统，可选）、`pytest`、`httpx`

**目录结构**（分层对应若依的 Controller / Service / Mapper）：

```
ruoyi/
├── TASK.md                 # 本文件
├── README.md               # 运行说明（Windows）
├── reference/              # 参考源码（不提交）
├── ruoyi-ui/               # 若依前端（原样使用）
└── backend/
    ├── .env / .env.example
    ├── requirements.txt
    ├── main.py             # 启动入口：uvicorn，端口 8080
    ├── sql/                # 从 RuoYi-Vue 复制的 ry_xxxx.sql（不需要 quartz.sql）
    ├── scripts/
    │   └── init_db.py      # 建库 ry-vue 并导入 SQL（不依赖 mysql 命令行）
    ├── app/
    │   ├── config.py       # pydantic-settings 读取 .env
    │   ├── core/
    │   │   ├── database.py     # engine、SessionLocal、get_db 依赖
    │   │   ├── redis_client.py
    │   │   ├── response.py     # AjaxResult / TableDataInfo 构造
    │   │   ├── exceptions.py   # ServiceException + 全局异常处理
    │   │   ├── security.py     # bcrypt、JWT
    │   │   ├── token_service.py# 登录态存 Redis、续期、删除
    │   │   ├── deps.py         # get_login_user、has_perm、has_role
    │   │   ├── data_scope.py   # 数据权限
    │   │   ├── oper_log.py     # @log 装饰器（对应若依 @Log）
    │   │   └── constants.py
    │   ├── models/         # SQLAlchemy ORM（对应 Mapper/Domain）
    │   ├── schemas/        # Pydantic 入参模型（camelCase 别名）
    │   ├── services/       # 业务逻辑（对应 ServiceImpl）
    │   └── routers/        # 路由（对应 Controller）
    │       ├── auth.py         # captchaImage / login / getInfo / getRouters / logout
    │       ├── system/         # user, role, menu, dept, post, dict, config
    │       └── monitor/        # operlog, logininfor, online
    └── tests/              # pytest 端到端测试
```

---

## 5. 前后端对接约定（必须严格遵守）

开始写接口前，先阅读 `reference/RuoYi-Vue3/src/utils/request.js`、`vite.config.js`、`src/store/modules/user.js`、`src/permission.js` 和 `src/api/` 下的文件，确认以下约定：

1. **端口与前缀**：前端开发时把 `/dev-api` 代理到 `http://localhost:8080` 并去掉前缀，所以后端监听 **8080**，路由**不加前缀**。
2. **统一返回**：HTTP 状态码一律 200，业务状态放在 JSON 的 `code` 字段：
   - 普通：`{"code": 200, "msg": "操作成功", "data": ...}`，部分接口把字段直接平铺在顶层（如 `token`、`user`、`roles`、`permissions`、`uuid`、`img`），以 Java Controller 为准
   - 分页：`{"code": 200, "msg": "查询成功", "total": N, "rows": [...]}`
   - 未登录/登录过期 `401`，无权限 `403`，业务错误 `500`，警告 `601`
3. **认证头**：`Authorization: Bearer <token>`。
4. **字段命名**：返回 JSON 全部 **camelCase**（`userId`、`userName`、`nickName`、`createTime`…），时间格式 `yyyy-MM-dd HH:mm:ss`。入参同样是 camelCase，Pydantic 用 `alias_generator=to_camel` + `populate_by_name=True`。
5. **分页参数**：`pageNum`、`pageSize`；时间范围参数形如 `params[beginTime]`、`params[endTime]`（需从 `request.query_params` 手动读取）。
6. **批量删除**：路径里用逗号分隔，如 `DELETE /system/user/2,3,4`。
7. **字典**：前端大量页面依赖 `GET /system/dict/data/type/{dictType}` 显示状态标签，必须实现。

---

## 6. 分阶段任务

每个阶段写完都要**对照 Java 源码检查一遍**，并补充对应的 pytest 测试。

### 阶段 1：项目骨架

- 配置读取、数据库连接、Redis 连接、统一返回、全局异常处理（`ServiceException` → `{"code":500,"msg":...}`；未捕获异常记录日志并返回 500）。
- ORM 模型：只为要用到的表建模，**字段名、顺序与 SQL 完全一致**：`sys_user`、`sys_role`、`sys_menu`、`sys_dept`、`sys_post`、`sys_user_role`、`sys_role_menu`、`sys_role_dept`、`sys_user_post`、`sys_dict_type`、`sys_dict_data`、`sys_config`、`sys_oper_log`、`sys_logininfor`。
- 提供一个 ORM 对象转 camelCase 字典的通用函数。
- `scripts/init_db.py`：用 PyMySQL 创建数据库 `ry-vue`（utf8mb4），导入 `sql/ry_xxxx.sql`。导入完成后用 Navicat 也能看到表。
- **验收**：`python main.py` 能启动，访问 `http://localhost:8080/docs` 能看到 Swagger。

### 阶段 2：登录认证（参考 `SysLoginController`、`SysLoginService`、`TokenService`、`JwtAuthenticationTokenFilter`、`CaptchaController`）

- **验证码** `GET /captchaImage`：生成算术验证码图片（Pillow），答案存 Redis `captcha_codes:{uuid}`，有效期 2 分钟；返回 `captchaEnabled`、`uuid`、`img`（base64，不带 `data:` 前缀，以前端代码为准）。校验后立即删除（一次性）。
- **登录** `POST /login`：校验验证码 → 检查账号是否被锁 → bcrypt 校验密码（若依库里是 `$2a$` 格式的 BCrypt，Python `bcrypt` 可直接校验）→ 检查用户状态 → 生成登录态。
  - **密码错误锁定**：Redis `pwd_err_cnt:{username}` 计数，错误 5 次锁定 10 分钟。
  - **登录态设计（重点）**：生成 uuid 作为 token 标识，JWT 里**只放这个 uuid**（`login_user_key`）；完整的登录用户信息（userId、deptId、用户名、权限集合、登录 IP、浏览器、系统、登录时间、过期时间）存 Redis `login_tokens:{uuid}`，有效期 30 分钟。
  - 更新用户最后登录 IP 和时间；记录登录日志（成功/失败都记）。
- **鉴权依赖** `get_login_user`：解析 JWT → 取 uuid → 读 Redis。读不到返回 401。**剩余有效期不足 20 分钟时自动续期到 30 分钟**（对应 `TokenService.verifyToken`）。
- **权限依赖**：`has_perm("system:user:list")`。超级管理员（userId=1）权限为 `*:*:*`，拥有一切权限；无权限返回 403。
- `GET /getInfo`：返回 `user`（含 dept、roles）、`roles`（角色 key 集合，admin 返回 `["admin"]`）、`permissions`；其他字段（如 `isDefaultModifyPwd`、`isPasswordExpired`）以前端 `user.js` 用到的为准，可返回 false。
- `GET /getRouters`：严格复刻 `SysMenuServiceImpl.buildMenus` 及 `getRouteName`、`getRouterPath`、`getComponent`、`isMenuFrame`、`isInnerLink`、`isParentView`、`innerLinkReplaceEach` 的规则（目录返回 `Layout`/`ParentView`、`alwaysShow`、`redirect: noRedirect`、`meta.noCache`、`meta.link` 等）。只取类型为 M、C 且状态正常的菜单，超级管理员取全部。
- `POST /logout`：删除 Redis 登录态，记录退出日志。
- **验收**：启动前端后，能用 `admin / admin123` 登录，看到左侧菜单。

### 阶段 3：系统管理（参考 `system` 包下各 Controller / ServiceImpl）

各接口的路径和返回结构**逐个对照** `reference/RuoYi-Vue3/src/api/system/*.js` 实现，包括其中的辅助接口（如 `deptTree`、`optionselect`、`roleMenuTreeselect`、`treeselect`、`authUser/*`、`changeStatus`、`resetPwd`、`profile` 等）。

- **用户**：列表（数据权限过滤）、详情（同时返回角色、岗位列表及已选 ID）、新增、修改、删除、重置密码、修改状态、分配角色、部门树、个人中心（查看、修改资料、修改密码）。
  - 校验：用户名唯一；**不允许操作超级管理员**（userId=1）；不能删除自己。
- **角色**：列表、详情、新增、修改（同步更新 `sys_role_menu`）、删除（已分配给用户的不能删）、修改状态、数据权限设置（`sys_role_dept`）、分配用户相关接口。不允许操作超级管理员角色（roleId=1）。
- **菜单**：列表、详情、树、角色菜单树（返回 `checkedKeys` 和 `menus`）、新增、修改、删除（有子菜单或已分配给角色时不能删）。同级菜单名唯一。
- **部门**：列表、排除某节点的列表、详情、新增、修改、删除。
  - **ancestors 维护（重点）**：新增时 `ancestors = 父.ancestors + "," + 父.deptId`；修改父部门时，**同步更新所有子孙部门的 ancestors**；有子部门或有用户时不能删除；包含未停用子部门时不能停用。
- **岗位**：完整增删改查 + `optionselect`（用户表单需要）。
- **字典**：只读。`/system/dict/data/type/{dictType}`、字典类型列表、字典数据列表、详情、optionselect。
- **参数**：只实现 `GET /system/config/configKey/{configKey}`（返回格式以 Java 为准）。
- **数据权限（重点，参考 `DataScopeAspect`）**：实现一个函数，根据当前用户的角色拼接 SQL 过滤条件：
  - `1` 全部：不过滤（命中即停止）
  - `2` 自定义：`dept_id IN (SELECT dept_id FROM sys_role_dept WHERE role_id = ?)`
  - `3` 本部门：`dept_id = 用户部门`
  - `4` 本部门及以下：`dept_id IN (SELECT dept_id FROM sys_dept WHERE dept_id = ? OR FIND_IN_SET(?, ancestors))`
  - `5` 仅本人：`user_id = 当前用户`
  - 多个角色之间用 `OR` 合并；超级管理员不过滤。应用于用户列表、部门列表、角色列表。
- **验收**：前端「系统管理」下的用户、角色、菜单、部门、岗位页面能正常增删改查；字典页面能正常显示列表。

### 阶段 4：系统监控

- **操作日志 `@log(title, business_type)` 装饰器（重点，对应若依 `@Log` 注解 + AOP）**：
  - 用 `functools.wraps` 包装路由函数，保证 FastAPI 仍能正确解析原函数的参数签名（同步路由的 wrapper 也必须是普通 `def`）。
  - 记录：模块标题、业务类型、请求方法、URL、操作人、IP、请求参数（排除 db、login_user 等依赖对象，脱敏密码字段）、返回结果、状态（成功/异常）、错误信息、耗时。
  - 获取当前 Request：用一个中间件把 Request 存入 `contextvars.ContextVar`，装饰器从中读取。
  - **异步写库**：用 `ThreadPoolExecutor` 在后台线程写日志，不阻塞接口响应（对应若依的 `AsyncManager`），写日志用独立的数据库 Session。
  - 给阶段 3 的增删改接口加上该装饰器。
- **操作日志 / 登录日志页面接口**：列表、删除、清空；登录日志的「解锁」接口清除 `pwd_err_cnt:{username}`。
- **在线用户**：扫描 Redis `login_tokens:*` 返回列表（支持按 IP、用户名筛选）；**强制下线**删除对应 key，被踢用户下一次请求返回 401。
- **验收**：前端「系统监控」下的在线用户、操作日志、登录日志页面正常工作；强制下线生效。

---

## 7. 自测要求

- 使用 pytest + FastAPI `TestClient`，连接**单独的测试库** `ry-vue-test`（测试前用 init_db 脚本重建）和 Redis **db 1**，不要污染开发数据。
- 至少覆盖：
  1. 验证码 → 登录 → getInfo → getRouters 全流程（验证码答案从 Redis 读取）
  2. 验证码错误、密码错误、连续错误 5 次被锁定
  3. 无 token / 错误 token 返回 401
  4. 普通用户（库中自带 `ry / admin123`，角色为普通角色）访问无权限接口返回 403
  5. 用户新增 → 查询 → 修改 → 删除；不能修改超级管理员
  6. 部门修改父节点后，子孙部门 ancestors 正确更新
  7. 数据权限：给角色设置不同数据范围，用户列表结果符合预期
  8. 增删改操作后 `sys_oper_log` 有记录
  9. 强制下线后原 token 返回 401
- 全部测试通过后，再**实际启动前后端**，告诉我在浏览器里检查哪些页面。

---

## 8. README.md（写给 Windows 用户）

包含：项目简介（注明"参考若依架构，使用 FastAPI 重写后端，前端复用 RuoYi-Vue3"）、技术栈、目录结构说明、与若依 Java 版的对应关系表，以及从零启动的步骤：

1. 配置 `.env`
2. 创建虚拟环境、安装依赖（含国内镜像）
3. 运行 `init_db.py` 初始化数据库（或用 Navicat 导入 SQL 的方法）
4. 启动后端：`python main.py`
5. 启动前端：`cd ruoyi-ui`、`npm install --registry=https://registry.npmmirror.com`、`npm run dev`
6. 浏览器访问前端地址，`admin / admin123` 登录
7. 常见问题：端口 80 被占用（可改 `vite.config.js` 的 port）、MySQL 连接失败、Redis 未启动、`cryptography` 缺失

以及「未实现的功能」列表，说明点击这些菜单会报错属于正常现象。

---

## 9. 不做的范围

代码生成、定时任务、服务监控、缓存监控、通知公告、Excel 导入导出、头像上传、字典与参数的增删改、注册功能。前端菜单保持原样，不需要隐藏。

---

## 10. 全部完成后

用简短的中文告诉我：

1. 实现了哪些模块、测试结果；
2. 我在浏览器里应该验证的操作清单；
3. 与若依 Java 版行为不一致、或者做了简化的地方。
