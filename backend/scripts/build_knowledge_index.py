"""
构建（增量）知识库索引：读取 docs/knowledge/*.md，分块、计算 Embedding、写 faiss 索引并切换数据库里的生效版本。

用法（在 backend/ 目录下）：
    .venv/Scripts/python.exe scripts/build_knowledge_index.py          # 用 .env 里配置的 Embedding 模型
    .venv/Scripts/python.exe scripts/build_knowledge_index.py --fake   # 用假 Embedding（只用于开发调试，不联网）
    .venv/Scripts/python.exe scripts/build_knowledge_index.py --force  # 忽略已有向量，全部重算

- 内容、模型、索引版本都没变时什么都不做；只改了部分文档时只为变化的分块调用 Embedding；
  Embedding 模型或 KNOWLEDGE_INDEX_VERSION 变化时自动全部重算。
- 文档内容会发送给配置的 Embedding 服务。
- 构建期间读取方继续使用旧索引；新索引文件完整写好后才在数据库里切换。不要同时运行两个构建。
"""
import argparse
import sys
from pathlib import Path

# 让脚本不论从哪个目录启动都能 import app.*
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import BACKEND_DIR, settings
from app.core.database import SessionLocal
from app.rag import embedding
from app.rag.documents import load_docs
from app.services import knowledge_service

DOCS_DIR = BACKEND_DIR.parent / "docs" / "knowledge"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="构建智能助手知识库索引")
    parser.add_argument("--fake", action="store_true", help="使用 FakeEmbeddingModel（不联网、不需要 Key）")
    parser.add_argument("--force", action="store_true", help="忽略已有向量，全部重新计算")
    parser.add_argument("--docs", type=Path, default=DOCS_DIR, help="知识文档目录")
    args = parser.parse_args(argv)

    docs = load_docs(args.docs)
    if args.fake:
        model = embedding.FakeEmbeddingModel()
    elif embedding.is_embedding_configured():
        model = embedding.get_embedding_model()
    else:
        raise SystemExit(embedding.MSG_NOT_CONFIGURED + "；只想在本地调试检索流程时可以加 --fake")

    db = SessionLocal()
    try:
        report = knowledge_service.build_index(db, docs, model, settings.knowledge_index_dir, force=args.force)
    finally:
        db.close()
    # 只打印数量和版本，不打印文档内容或任何配置值
    print(f"文档 {len(docs)} 篇，分块 {report.chunk_count} 个；模型 {model.name}")
    if report.published:
        print(f"已发布 {report.build_id}（{report.reason}）：计算 Embedding {report.embedded} 段，复用 {report.reused} 个分块")
    else:
        print(f"无需重建：{report.reason}（当前 {report.build_id}）")


if __name__ == "__main__":
    main()
