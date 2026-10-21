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
| `app/core/deps.py` | `JwtAuthenticationTokenFilter` + `@PreAuthorize("@ss.hasPermi(...)")` |
| `app/core/data_scope.py` | `@DataScope` + `DataScopeAspect` |
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

## 运行测试

```powershell
cd backend
.venv\Scripts\python.exe -m pytest tests/ -v
```

测试连接独立的 `ry-vue-test` 库和 Redis db 1，不会污染开发数据（每次运行会重建测试库表结构）。

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
