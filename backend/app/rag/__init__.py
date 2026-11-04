"""
智能助手知识库（RAG）。

模块职责：
- documents.py    读取 docs/knowledge/*.md：解析并校验 frontmatter、按章节分块
- embedding.py    Embedding 接口：OpenAI 兼容实现 + 测试用的 FakeEmbeddingModel、L2 归一化
- index_store.py  faiss 索引文件的写入、发布前的完整性、读取方的加载与一致性校验
索引构建流程在 services/knowledge_service.py，命令行入口是 scripts/build_knowledge_index.py。
知识库只读：这里没有任何修改业务数据的代码。
"""
