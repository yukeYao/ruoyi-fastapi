"""
知识库（RAG）评估：手动运行，会调用真实对话模型和 Embedding 并产生费用；不进 pytest/CI。

- retrieval_dataset.json：检索评估（dev 只用于校准 τv、τb、最少命中词数，test 只用于报告）
- e2e_dataset.json：端到端评估（规则问答、结合实时工具、无答案、权限受限、注入、伪造引用、追问、SSE 一致性、权限建议）
- metrics.py：评分与汇总（纯函数，离线测试覆盖）
- retrieval.py：收集每个问题在可读集合内的 BM25 / 向量候选（需要数据库、索引和 Embedding）
- runner.py：端到端运行器，按方案限制工具、切换检索方式
- run.py：命令行入口；--plan 只打印规模和费用估算，不调用任何 API
"""
