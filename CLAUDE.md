# CLAUDE.md

本文件是给 AI 编程助手（Claude Code）的项目规范，每次会话开始时自动加载。修改代码前请先读完。

## 项目是什么

用 FastAPI 复现若依（RuoYi）后台管理系统的后端（精简学习版）。

- 前端：`ruoyi-ui/`，若依官方 RuoYi-Vue3，**原样复用，不要修改其中任何业务代码**。
- 后端：`backend/`，FastAPI + SQLAlchemy（同步）+ MySQL + Redis。
- 数据库：若依官方建表脚本 `backend/sql/ry_20260417.sql`，表结构不改。
- 唯一标准：接口路径、参数、返回格式以前端 `ruoyi-ui/src/api/**/*.js` 为准；业务规则以若依 Java 版（RuoYi-Vue）源码为准。不确定时先去读对应源码，不要凭印象实现。

## 常用命令（在 `backend/` 目录下）

```bash
# Windows 激活虚拟环境
.venv\Scripts\activate

python main.py                 # 启动后端，监听 8080，带自动重载
python scripts/init_db.py      # 建库 ry-vue 并导入若依 SQL（会覆盖数据）
pytest -q                      # 全部测试（需要本机 MySQL 和 Redis 在运行）
pytest -q tests/test_stage2_login.py   # 单个文件
python -m evals.run_eval --runs 3      # 智能助手评估：调用真实模型，只能手动运行
python scripts/build_knowledge_index.py --fake   # 构建知识库索引（--fake 不联网；不加则用 .env 里的 Embedding）
python -m evals.rag.run --plan         # 知识库评估的规模与费用估算（不调用 API）；--stage 才会真实运行
```

测试会自动使用独立的测试库 `ry-vue-test` 和 Redis db 1，不会影响开发数据。

## 目录约定

| 目录 | 职责 | 对应若依 Java |
|---|---|---|
| `app/routers/` | 取参数、声明权限、调用 service、包装返回 | Controller |
| `app/services/` | 业务规则与校验 | ServiceImpl |
| `app/models/` | ORM 模型，一表一文件 | Domain + Mapper |
| `app/schemas/` | Pydantic 入参模型，继承 `CamelModel` | 请求体 |
| `app/core/` | 与业务无关的基础设施 | common / framework |
| `app/agent/` | 智能助手：工具规格、权限策略、审批、LangGraph 图、引用校验 | — |
| `app/rag/` | 知识库：文档解析与分块、Embedding、faiss 索引文件、分词、BM25、RRF | — |
| `evals/` | 智能助手评估（数据集、运行器、评分、报告模板）；`evals/rag/` 是知识库评估 | — |

## 编码规则（违反任何一条都会导致前端出错或安全问题）

1. **同步写法**：路由、依赖、service 一律用普通 `def`，不要用 `async def`。
2. **统一返回**：用 `core/response.py` 的 `ajax_success` / `table_data_info`。HTTP 状态码一律 200，业务状态放在 `code` 字段（401 未登录、403 无权限、500 业务错误）。业务错误一律 `raise ServiceException(msg)`。
3. **字段命名**：返回 JSON 用 camelCase（`model_to_camel_dict`），时间格式 `yyyy-MM-dd HH:mm:ss`；入参模型继承 `schemas/base.py` 的 `CamelModel`。
4. **路由路径与前端完全一致，包括末尾斜杠**。项目已设置 `redirect_slashes=False`。固定路径（如 `/list`）必须定义在路径参数（如 `/{id}`）之前。
5. **权限**：需要权限的接口用 `Depends(has_perm("模块:功能:操作"))`，权限字符与 Java 版 `@PreAuthorize` 一致；只需登录的用 `Depends(get_login_user)`。
6. **操作日志**：增删改接口加 `@log(title=..., business_type=...)`，并且必须写在 `@router.xxx` 的**下面**。
7. **逻辑删除**：查询用户、角色、部门时必须带 `del_flag == DEL_FLAG_NORMAL`。
8. **审计字段**：新增调用 `fill_create_fields`，修改调用 `fill_update_fields`（`core/audit.py`）。
9. **唯一性校验**：新增和修改都要做，修改时排除自身，并排除已删除数据。
10. **数据权限**：列表查询用 `build_data_scope_filter`，表别名固定为用户 `u`、部门 `d`；按 ID 修改、删除前调用对应的 `check_*_data_scope`，防止水平越权。
11. **超级管理员**：userId=1、roleId=1 不允许修改、删除、停用（`check_user_allowed` / `check_role_allowed`）。
12. **常量**：状态值、Redis key 前缀、过期时间等写在 `core/constants.py`，不要在代码里写魔法字符串。
13. 拼接到 SQL 的值只能来自服务端并经过 `int()` 转换；任何用户输入都必须走 ORM 参数化查询。

## 智能助手（Agent）模块规范

设计说明见 `docs/agent-design.md`。改动 `app/agent/`、`services/agent_*`、`routers/agent.py` 时必须遵守：

1. **安全判断只在服务端**：权限、数据范围、目标解析、审批都不能依赖模型遵守提示词。提示词只用来提高体验。
2. **工具**：只能调用现有 service，不提供任意 SQL、任意 HTTP、代码执行类工具。参数模型继承 `ToolArgs`（`extra="forbid"`），
   声明 `perm`、输出字段白名单和脱敏字段。只读工具执行前再调 `check_perm`（绑定过滤不是安全边界）。
3. **工具结果**：用 `serialization.tool_success / tool_error`。数据库里的数据放在 `untrusted_data`；
   服务端生成的说明和状态放在外层（如审批结果的 `status / executed`、空结果的 `note`）。禁止返回密码、tokenId、完整手机号和邮箱。
4. **写操作**：模型只提供 `user_name` 等业务参数；目标由服务端精确解析并冻结进 `target_json` 和 `args_hash`。
   范围外和不存在必须返回同一句话。一个 action 只对应一个副作用。
5. **审批状态机**：agent_action 的每次状态迁移都用 `agent_action_service.transition`（条件 UPDATE + rowcount）。
   approval 节点只能调用 `interrupt()`，不能有副作用；所有副作用放在 execute 节点。EXECUTING 不自动重试。
6. **结果以 agent_action 为准**：接口返回的状态、页面显示的状态都取自 agent_action 和 checkpoint，不解析模型回复。
   模型总结失败不能把已到终态的操作报成"处理失败"。
7. **运行时上下文**：登录态、模型只放在 `AgentContext`（Runtime context，不落盘），不能写进 state 或 config。
8. **时限**：运行锁 `AGENT_RUN_LOCK_SECONDS` 必须大于运行时限加一次模型超时；模型客户端 `max_retries=0`。
9. **日志与异常**：模型异常经 `ModelCallError` 脱敏后再抛出；日志里不能出现 Key、JWT、tokenId。
10. **前端**：只允许 `views/agent/chat/index.vue` 和 `api/agent/chat.js`；内容一律纯文本渲染（不用 v-html，不渲染 Markdown）。
11. **能力边界与循环保护**：缺少的能力由 `policy.capability_boundary` 从工具注册表生成，拼进本次运行的系统上下文，不写进 state；无权限的工具仍不绑定。循环保护（`graph.tools_node` / `propose_node`）只决定"何时停止"，不能用来放宽权限或审批，也不能推断用户意图替换写操作。
12. **知识库**：文档在 `docs/knowledge/*.md`（frontmatter 的 required_perms 需全部满足）。权限过滤必须在检索之前完成；
    来源只由服务端按本轮 search_knowledge 结果生成（`agent/citations.py`），只表示"内容来自本轮检索"，不代表事实已核验。
    `AgentContext.tool_names` 和 `KNOWLEDGE_RETRIEVAL_MODE` 只用于评估对照，只能收窄、不能放宽权限。
13. **测试**：pytest 用 `ScriptedChatModel`（`scripted_model` fixture）和 `FakeEmbeddingModel`，不得调用真实模型或 Embedding，也不需要 Key。
    行为测试检查数据库、Redis、checkpoint 的真实状态，不检查提示词文字。真实模型只在 `evals/` 和手动冒烟中使用，且只连测试库。

## 工作流程

- **改动较大时先给方案**：涉及多个文件、或修改数据库查询逻辑时，先说明改哪些文件、为什么、怎么验证，等我确认后再动手。
- **修 bug 先写测试**：先写一个能复现问题的测试并确认它失败，再修复，最后确认测试通过。
- **改完必须跑测试**：至少跑相关的测试文件；提交前跑全部测试。项目配置了 Stop hook，会在你结束回复时自动跑全部测试，失败会把结果反馈给你。
- **举一反三**：修复一个问题后，检查其他模块是否有同类问题，并列出检查结果。
- **提交信息用中文**，说明做了什么和为什么。
- 用中文和我交流，代码注释也用中文，注释要解释"为什么这么做"。

## 不做的范围

代码生成、定时任务、服务监控、缓存监控、通知公告管理、Excel 导入导出、头像上传、字典与参数的增删改、注册。除非我明确要求，否则不要实现。

## 已知问题（待修复）

- 密码错误计数、验证码校验是"先读后写"，不是原子操作。

已修复（回归测试见 `tests/test_known_issues_fix.py`）：在线用户列表改用 `SCAN`；`check_user_data_scope` 改为按 ID 直接查询。

智能助手的已知局限见 `docs/agent-design.md` 第 8 节。
