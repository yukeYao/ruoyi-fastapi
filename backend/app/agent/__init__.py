"""
后台智能助手（LangGraph）。

模块职责：
- context.py        每次请求的运行时上下文（登录态、模型），不进入 checkpoint
- state.py          写入 checkpoint 的图状态，只放可序列化的会话信息
- model.py          模型工厂 + 测试用的可脚本化 Fake 模型
- serialization.py  工具结果的字段白名单、脱敏、截断
- tools.py          工具注册表：名称、风险级别、权限、入参模型、执行函数
- policy.py         工具过滤、调用分类、执行与各种上限
- graph.py          StateGraph 定义
- checkpointer.py   SqliteSaver 单例
路由只负责取参数和包装返回，业务流程在 services/agent_service.py。
"""
