"""
faiss 索引文件层（不连数据库）：写一次构建、读一次构建、在允许集合内检索。

每次构建是一对不可变文件，按 build_id 命名：

    index-<build_id>.faiss   向量（IndexIDMap2(IndexFlatIP)，向量已 L2 归一化，内积 = 余弦相似度）
    index-<build_id>.json    清单：build_id、index_version、embedding_model、dimension、chunk_count、vectors_digest

发布顺序（见 knowledge_service.build_index）：先把这一对文件完整写好（写临时文件再 os.replace 原子改名），
再在数据库事务里切换"当前生效的 build_id"。读取方只按数据库里的 build_id 找文件，
所以只会看到旧的完整构建或新的完整构建，看不到写了一半的文件。

不用 faiss.write_index / read_index：Windows 上路径含中文时它会直接报错（阶段 0 实测），
改为 serialize_index 成字节后用 Python 读写文件。
"""
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import faiss
import numpy as np

from app.rag.embedding import l2_normalize

FILE_PREFIX = "index-"


class IndexFileError(RuntimeError):
    """索引文件缺失、损坏或与清单不一致"""


@dataclass(frozen=True)
class IndexManifest:
    build_id: str
    index_version: str
    embedding_model: str
    dimension: int
    chunk_count: int
    vectors_digest: str


def index_files(index_dir: Path, build_id: str) -> tuple[Path, Path]:
    return index_dir / f"{FILE_PREFIX}{build_id}.faiss", index_dir / f"{FILE_PREFIX}{build_id}.json"


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    # 同一目录内改名是原子的：读取方要么看不到这个文件，要么看到完整内容
    os.replace(tmp, path)


def write_build(index_dir: Path, manifest: IndexManifest, vector_ids: np.ndarray, vectors: np.ndarray) -> None:
    """写入一次构建。向量在这里再归一化一次：调用方忘了归一化，内积就不再等于余弦相似度"""
    vectors = l2_normalize(vectors)
    if vectors.shape != (manifest.chunk_count, manifest.dimension) or len(vector_ids) != manifest.chunk_count:
        raise IndexFileError("向量数量或维度与清单不一致")
    index = faiss.IndexIDMap2(faiss.IndexFlatIP(manifest.dimension))
    if manifest.chunk_count:
        index.add_with_ids(vectors, np.ascontiguousarray(vector_ids, dtype="int64"))
    index_dir.mkdir(parents=True, exist_ok=True)
    faiss_path, manifest_path = index_files(index_dir, manifest.build_id)
    _atomic_write(faiss_path, faiss.serialize_index(index).tobytes())
    _atomic_write(manifest_path, json.dumps(asdict(manifest), ensure_ascii=False).encode("utf-8"))


class LoadedIndex:
    def __init__(self, manifest: IndexManifest, index):
        self.manifest = manifest
        self._index = index

    @property
    def ntotal(self) -> int:
        return int(self._index.ntotal)

    def reconstruct(self, vector_id: int) -> np.ndarray:
        return self._index.reconstruct(int(vector_id))

    def search(self, query: np.ndarray, k: int, allowed_ids: list[int]) -> list[tuple[int, float]]:
        """
        只在 allowed_ids 里检索（IDSelectorBatch 预过滤），不是搜全量再过滤：
        范围外的向量既不会出现在结果里，也不会占用 top-k 名额。返回 [(vector_id, 余弦相似度)]，按相似度降序
        """
        if not allowed_ids or k <= 0:
            return []
        query = l2_normalize(query)
        if query.shape[1] != self.manifest.dimension:
            raise IndexFileError("查询向量维度与索引不一致")
        allowed = np.array(sorted(set(allowed_ids)), dtype="int64")
        params = faiss.SearchParameters(sel=faiss.IDSelectorBatch(allowed))
        scores, ids = self._index.search(query, min(k, len(allowed)), params=params)
        return [(int(i), float(s)) for i, s in zip(ids[0], scores[0]) if i != -1]


def read_build(index_dir: Path, build_id: str) -> LoadedIndex:
    faiss_path, manifest_path = index_files(index_dir, build_id)
    try:
        manifest = IndexManifest(**json.loads(manifest_path.read_text(encoding="utf-8")))
        index = faiss.deserialize_index(np.frombuffer(faiss_path.read_bytes(), dtype="uint8"))
    except FileNotFoundError:
        raise IndexFileError("索引文件不存在") from None
    except (ValueError, TypeError, RuntimeError):
        raise IndexFileError("索引文件损坏") from None
    if manifest.build_id != build_id:
        raise IndexFileError("索引清单与文件名不一致")
    if index.ntotal != manifest.chunk_count or index.d != manifest.dimension:
        raise IndexFileError("索引向量数或维度与清单不一致")
    return LoadedIndex(manifest, index)


def remove_old_builds(index_dir: Path, keep: set[str]) -> None:
    """删除不在 keep 里的旧构建（及残留的临时文件）。删除失败（Windows 上文件被占用）就留到下次"""
    for path in index_dir.glob(f"{FILE_PREFIX}*"):
        build_id = path.name[len(FILE_PREFIX):].split(".", 1)[0]
        if build_id not in keep or path.name.endswith(".tmp"):
            try:
                path.unlink()
            except OSError:
                pass
