# RuoYi FastAPI 后端（精简学习版）

参考若依（RuoYi）架构，使用 **FastAPI** 重写后端，前端复用官方 **RuoYi-Vue3** 源码（不改业务代码）。
这是一个用来学习后端原理、准备校招面试的练手项目，刻意保持精简：接口路径、参数、返回格式与若依
Java 版对齐，但只实现了核心功能（登录认证 + 系统管理 + 系统监控），详见「未实现的功能」一节。

## 技术栈

| 分类 | 选型 |
|---|---|
| Web 框架 | FastAPI + Uvicorn（同步写法，不用 async） |
| ORM | SQLAlchemy 2.0（同步） |
| 数据库 | MySQL 8.4（PyMySQL 驱动） |
| 缓存 / 登录态 | Redis（redis-py，RESP2 协议） |
| 认证 | JWT（PyJWT）+ Redis 存储完整登录态 |
| 密码加密 | bcrypt（兼容若依库里 Spring Security 生成的 `$2a$` 哈希） |
| 验证码 | Pillow 画图 |
| 测试 | pytest + FastAPI TestClient |
| 前端 | RuoYi-Vue3（原样使用，不修改） |

## 目录结构

```
ruoyi/
├── reference/              # 参考源码（RuoYi-Vue Java 源码 + RuoYi-Vue3），不提交到 git，仅供查阅对照
├── ruoyi-ui/                # 若依前端，原样使用
└── backend/
    ├── .env / .env.example  # 配置（.env 不提交）
    ├── requirements.txt
    ├── main.py              # 启动入口，监听 8080
    ├── sql/                 # 数据库建表 SQL
    ├── scripts/init_db.py   # 建库 + 导入 SQL（不依赖本机 mysql 命令行）
    ├── app/
    │   ├── config.py
    │   ├── core/            # 数据库/Redis连接、统一返回、异常处理、JWT、登录态、数据权限、操作日志装饰器
    │   ├── models/          # SQLAlchemy ORM（对应 Mapper/Domain）
    │   ├── schemas/         # Pydantic 入参模型（camelCase 别名）
    │   ├── services/        # 业务逻辑（对应 ServiceImpl）
    │   └── routers/         # 路由（对应 Controller）
    │       ├── auth.py          # 登录认证
    │       ├── system/          # 用户/角色/菜单/部门/岗位/字典/参数
    │       └── monitor/         # 操作日志/登录日志/在线用户
    └── tests/               # pytest 端到端测试
```

## 与若依 Java 版的对应关系

| 本项目 | 若依 Java 版 |
|---|---|
| `app/routers/*` | `*Controller.java` |
| `app/services/*` | `*ServiceImpl.java` |
| `app/models/*` | `Mapper.xml` / `Domain` |
| `app/core/response.py` | `AjaxResult` / `TableDataInfo` |
| `app/core/exceptions.py` | `ServiceException` + 全局异常处理器 |
| `app/core/security.py` + `token_service.py` | `TokenService` + JWT |
| `app/core/deps.py` | `JwtAuthenticationTokenFilter` + `@PreAuthorize("@ss.hasPermi(...)")` + `PermissionContextHolder` |
| `app/core/data_scope.py` | `@DataScope` + `DataScopeAspect` |
| `app/core/audit.py` | `BaseEntity` 的 createBy/createTime/updateBy/updateTime + Mapper 里的 `sysdate()` |
| `app/core/oper_log.py` | `@Log` + `LogAspect` |
| `app/services/menu_service.py` 里的 `build_menus` | `SysMenuServiceImpl.buildMenus` |

## 从零启动（Windows）

### 1. 配置 `.env`

```
cd backend
copy .env.example .env
```

按实际情况修改 `MYSQL_PASSWORD`。

### 2. 创建虚拟环境、安装依赖

```powershell
cd backend
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 3. 初始化数据库

```powershell
.venv\Scripts\python.exe scripts\init_db.py
```

会自动创建 `ry-vue` 数据库（utf8mb4）并导入 `sql/ry_20260417.sql`。也可以用 Navicat 手动导入这份 SQL。

### 4. 启动后端

```powershell
.venv\Scripts\python.exe main.py
```

浏览器打开 `http://localhost:8080/docs` 能看到 Swagger 就说明启动成功。

### 5. 启动前端

```powershell
cd ruoyi-ui
npm install --registry=https://registry.npmmirror.com
npm run dev
```

### 6. 登录

浏览器访问前端地址（终端会打印，默认 `http://localhost:80`），用 `admin / admin123` 登录。

### 7. 常见问题

- **端口 80 被占用**：改 `ruoyi-ui/vite.config.js` 里 `server.port`。
- **MySQL 连接失败**：检查 `.env` 里的密码、MySQL 服务是否启动。
- **Redis 连接报 `unknown command 'HELLO'`**：说明本机 Redis 版本较旧（&lt;6.0）不支持 RESP3，
  `app/core/redis_client.py` 里已经显式指定 `protocol=2` 规避，正常不会再出现。
- **`cryptography` 缺失**：MySQL 8.4 默认用 `caching_sha2_password` 认证，PyMySQL 需要这个包，
  已经在 `requirements.txt` 里。
- **修改了角色的数据权限/菜单/状态后看起来没生效**：和若依一样，角色、权限、数据范围在登录时写入
  Redis 登录态，需要对应用户重新登录才会生效。

## 运行测试

```powershell
cd backend
.venv\Scripts\python.exe -m pytest tests/ -v
```

测试连接独立的 `ry-vue-test` 库和 Redis db 1，不会污染开发数据（每次运行会重建测试库表结构）。

除了按开发阶段划分的 `test_stage*.py`，下面「开发中遇到的问题与解决」里的每个问题都有对应的回归测试文件。

## 未实现的功能

以下菜单点击会报错（接口未实现），属于正常现象：代码生成、定时任务、服务监控、缓存监控、
通知公告、Excel 导入导出、头像上传、字典与参数的增删改、注册功能。

## 简化说明

- **登录地点 / IP 归属地**：若依用 ip2region 或第三方接口查真实地理位置，这里简化成只区分
  "内网IP"和"未知地点"，不接外部服务。
- **浏览器/操作系统解析**：若依用专门的 UA 解析库做了很细致的版本号处理，这里用 Python
  `user-agents` 库做了简化版解析，格式可能跟若依不完全一致。
- **getInfo 里的 `isDefaultModifyPwd` / `isPasswordExpired` / `pwdChrtype`**：按 TASK 约定简化成
  固定值（`false` / `false` / `"0"`），不读取 `sys_config` 里对应的密码策略配置。
- **IP 黑名单登录校验**：若依支持配置登录IP黑名单，这里未实现（配置项 `sys.login.blackIPList`
  读取了但不做匹配拦截）。
- **部门/菜单的 `updateSort` 拖拽排序接口**：Java 源码里有，但 RuoYi-Vue3 这版前端页面并未调用
  （没有拖拽排序 UI），所以没有实现对应后端接口。
- **首页铃铛的通知公告接口**：只为铃铛组件补了 `/system/notice/listTop`、`markRead`、`markReadAll`
  三个最小实现（需登录，`listTop` 固定返回空列表和 `unreadCount: 0`，标记已读直接返回成功不落库），
  让首页不再报 404；通知公告菜单本身的增删改查仍未实现。
- **个人中心修改资料/密码的 `update_by`**：若依这两个接口只更新 `update_time`，不写 `update_by`；
  这里统一填成用户本人，便于追溯。

## 开发中遇到的问题与解决

功能"能跑"之后，用前端实际点击、对照 Java 源码逐项核对时发现的问题。每条都补了回归测试，
撤掉修复后对应测试会失败。

### 1. 末尾斜杠导致 307 重定向，绕过 vite 代理

- **现象**：用户管理点"新增"，前端弹"后端接口连接异常"；后端日志里 `GET /system/user/` 返回 307。
- **原因**：前端新增时请求 `'/system/user/' + parseStrEmpty(userId)`，`userId` 为空，于是 URL 带末尾斜杠；
  后端只注册了 `/system/user`。FastAPI 默认 `redirect_slashes=True`，会 307 重定向到不带斜杠的
  **绝对地址**：vite 代理开了 `changeOrigin` 且去掉了 `/dev-api` 前缀，后端收到的 Host 是 `localhost:8080`，
  于是 `Location` 指向后端本身而不是前端代理，浏览器跟随时绕过了 vite 代理，变成跨域请求被拦截。
  Java 版是 `@GetMapping(value = { "/", "/{userId}" })`，本来就匹配带斜杠的路径。
- **解决**：
  - 路由改为 `"/"` 和 `"/{user_id}"`，与 Java 一致；
  - `FastAPI(redirect_slashes=False)`，斜杠不一致时直接 404，不再隐式重定向，问题能第一时间暴露；
  - 逐个比对前端 `src/api` 下全部 121 个请求与后端路由，确认没有其他斜杠不一致。
- **测试**：`test_trailing_slash.py`，其中一个用例会自动扫描前端 `src/api` 防止回归。

### 2. 审计字段缺失：新增的数据创建时间为空

- **现象**：新增用户后，列表里"创建时间"一栏为空。
- **原因**：若依的 `create_by`/`update_by` 由 Controller 用 `getUsername()` 设置，而
  `create_time`/`update_time` 是 Mapper XML 里的 `sysdate()` 写入的，不在 Java 业务代码里，
  照着 Controller/Service 翻译时漏掉了这两个时间字段。
- **解决**：新增 `app/core/audit.py`，提供 `fill_create_fields` / `fill_update_fields`（对应 `BaseEntity`），
  用户、角色、菜单、部门、岗位的所有新增和修改接口统一调用，包括重置密码、修改状态、数据权限、
  个人中心等"修改类"接口。
- **测试**：`test_audit_fields.py`。

### 3. 逻辑删除的数据没有被过滤

- **现象**：删除用户后再新增一个同名用户，新用户登录提示"对不起，您的账号已被删除"。
- **原因**：`sys_user` / `sys_role` / `sys_dept` 是逻辑删除（`del_flag = '2'`），记录还在表里。
  登录按用户名查用户时没有加 `del_flag = '0'`，先查到了旧的已删除记录。若依 Mapper 里
  `selectUserByUserName` 是带这个条件的。
- **解决**：对照 Mapper XML 检查这三张表的全部查询，补齐 4 处：登录查用户（`selectUserByUserName`）、
  登录取角色权限（`selectRolePermissionByUserId`）、个人中心角色组（`selectRolesByUserName`）、
  停用部门时统计正常子部门（`selectNormalChildrenDeptById`，否则已删除的子部门会挡住停用）。
  按 ID 查详情等 Java 本身不过滤的查询保持一致不加。
- **测试**：`test_soft_delete.py`。

### 4. 唯一性校验缺失：部门名称、菜单路由等

- **现象**：同一上级下可以建出两个同名部门。
- **原因**：翻译时按接口逐个实现，没有系统地对照 Java 的校验方法，漏掉了一部分。
- **解决**：对照用户、角色、菜单、部门、岗位各 ServiceImpl 的全部 `check*Unique` / `check*Allowed`
  （共 12 个）逐个核对，补齐：
  - `checkDeptNameUnique`：同一上级下部门名称唯一（排除已删除）；
  - `checkRouteConfigUnique`：同级路由地址唯一、根目录路由地址全局唯一、路由名称全局唯一
    （为空时用路由地址代替，忽略大小写）。规则本身不对称——非根菜单只和同级比路由地址，根菜单要和
    所有目录/菜单比——这里按 Java 原样保留；
  - 个人中心修改资料时漏调了手机号、邮箱唯一校验。
- **测试**：`test_unique_checks.py`，其中一个用例确认所有种子菜单原样保存都能通过路由校验。

### 5. 数据权限覆盖不全，按 ID 操作可以越权（水平越权）

- **现象**：数据权限为"本部门"的用户，用户列表已经正确过滤，但左侧部门树仍显示全部部门；
  进一步检查发现，只要知道范围外用户的 ID，就能直接调接口重置他的密码、修改状态、给他分配角色。
- **原因**：若依的 `@DataScope` 是 AOP 实现的，不仅标注的 5 个查询方法会过滤，经 AOP 代理调用它们的
  `selectDeptTreeList`、`selectRoleAll` 以及 `checkUserDataScope` / `checkRoleDataScope` /
  `checkDeptDataScope` 也都受数据权限约束。翻译时只给"列表"接口加了过滤，漏掉了这些间接调用，
  也漏掉了部分接口里按 ID 操作前的 `check*DataScope` 校验——列表看不到，不代表接口调不到。
- **解决**：
  - 查询：用户管理部门树（并补上 `system:user:list` 权限）、新增/修改用户表单与分配角色页的角色列表、
    角色下拉框改为按数据权限过滤；角色查询抽成 `_role_query` 供列表、`selectRoleAll`、校验共用；
  - 校验：新增/修改用户补 `checkDeptDataScope` + `checkRoleDataScope`，重置密码、修改状态补
    `checkUserDataScope`，分配角色补 `checkUserDataScope` + `checkRoleDataScope`。
- **测试**：`test_data_scope.py`。

### 6. 数据权限没有按角色状态和接口权限筛选角色

- **现象**：多角色用户看到的数据比若依多。例如用户同时有"全部数据权限"角色 A（只有角色管理菜单）和
  "本部门"角色 B（有用户管理菜单），查用户列表时能看到全部用户；A 被停用后依然如此。
- **原因**：之前把用户所有角色的数据范围直接 OR 合并。若依的 `DataScopeAspect` 会：
  跳过停用角色；读取 `PermissionContextHolder` 里当前接口的权限字符（由 `@ss.hasPermi` 写入），
  只让拥有该权限的角色参与计算——查用户列表只看有 `system:user:list` 的角色。
- **解决**：
  - 登录态的每个角色记下 `status` 和自己的 `permissions`（对应 `getMenuPermission` 里的 `role.setPermissions`）；
  - `has_perm` 返回带当前权限字符的登录态副本，对应 `PermissionContextHolder.setContext`。
    没有用 ContextVar：同步依赖在线程池里执行，里面设置的值传不到路由函数；
  - `build_data_scope_filter` 按 `dataScopeFilter` 重写：跳过停用角色和不含当前权限字符的角色、
    同一数据范围只算一次、多个自定义角色合并为 `role_id IN (...)`、没有角色参与时看不到任何数据。
- **测试**：`test_data_scope_filter.py`（过滤规则单元测试 + 多角色接口测试）。

### 经验

- **对照 Java 时不能只看 Controller/Service**：时间字段在 Mapper XML 的 `sysdate()` 里、软删除条件在
  SQL 的 `where` 里、数据权限在 AOP 切面里，这些"隐式行为"最容易漏。
- **按清单核对比按接口实现更可靠**：列出 Java 的全部 `check*` 方法、全部 `@DataScope` 方法及其调用方，
  逐个打勾，比边写接口边想要不要校验漏得少。
- **列表过滤 ≠ 接口安全**：前端看不到的数据，后端按 ID 的接口仍然要自己校验。
