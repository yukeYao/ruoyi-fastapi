-- ----------------------------------------------------------------------------
-- 智能助手知识库（RAG）新增的表。不修改若依原有表结构。
-- 本文件必须可以重复执行：先 DROP 再 CREATE，重新导入会清空知识库元数据；
-- 本地向量索引文件（data/knowledge_index/）由 scripts/init_db.py 手动执行时一并删除，
-- 之后运行 scripts/build_knowledge_index.py 重新构建。
-- 表里只有文档内容和元数据，不保存 Embedding 向量（向量在 faiss 索引文件里），也不保存任何密钥。
-- ----------------------------------------------------------------------------

-- 知识文档：每篇 docs/knowledge/*.md 一行，frontmatter 原样入库
drop table if exists agent_knowledge_doc;
create table agent_knowledge_doc (
  doc_id            varchar(64)     not null                   comment '文档ID（与文件名一致）',
  title             varchar(100)    not null                   comment '标题',
  version           int             not null                   comment '文档版本（frontmatter）',
  required_perms    varchar(500)    not null default '[]'      comment '阅读所需权限（JSON 数组，需全部满足；空数组表示所有智能助手用户可读）',
  sources           varchar(1000)   not null default '[]'      comment '内容来源文件（JSON 数组，相对仓库根目录）',
  content_hash      char(64)        not null                   comment '整篇文档的 SHA-256',
  chunk_count       int             not null                   comment '分块数',
  create_time       datetime                                   comment '创建时间',
  update_time       datetime                                   comment '更新时间',
  primary key (doc_id)
) engine=innodb comment = '智能助手知识文档表';

-- 知识分块：检索的最小单位。required_perms 冗余一份文档的权限，检索前按它确定可读集合，不用再关联文档表。
-- vector_id 是该分块在当前生效索引里的向量 ID，每次发布索引时重新分配。
drop table if exists agent_knowledge_chunk;
create table agent_knowledge_chunk (
  chunk_id          varchar(100)    not null                   comment '分块ID（doc_id#序号）',
  doc_id            varchar(64)     not null                   comment '文档ID',
  title             varchar(100)    not null                   comment '文档标题',
  section           varchar(200)    not null default ''        comment '章节标题',
  seq               int             not null                   comment '文档内序号',
  content           text            not null                   comment '分块内容',
  content_hash      char(64)        not null                   comment 'Embedding 输入文本的 SHA-256，未变化时复用向量',
  required_perms    varchar(500)    not null default '[]'      comment '阅读所需权限（JSON 数组，需全部满足）',
  vector_id         bigint(20)      not null                   comment 'faiss 向量ID',
  embedding_model   varchar(100)    not null                   comment '生成向量的 Embedding 模型',
  index_version     varchar(32)     not null                   comment '分块与索引规则版本',
  create_time       datetime                                   comment '创建时间',
  update_time       datetime                                   comment '更新时间',
  primary key (chunk_id),
  unique key uk_agent_kchunk_vector (vector_id),
  key idx_agent_kchunk_doc (doc_id, seq)
) engine=innodb comment = '智能助手知识分块表';

-- 当前生效的索引：只有一行（id=1）。构建脚本先把新索引文件完整写好，再在同一个事务里替换分块并更新这一行，
-- 提交即切换；读取方按 build_id 发现新版本并重新加载，并用 vectors_digest 校验索引文件与分块表一致。
drop table if exists agent_knowledge_index;
create table agent_knowledge_index (
  id                tinyint         not null                   comment '固定为 1',
  build_id          varchar(64)     not null                   comment '本次发布的构建ID（索引文件名）',
  index_version     varchar(32)     not null                   comment '分块与索引规则版本',
  embedding_model   varchar(100)    not null                   comment 'Embedding 模型',
  dimension         int             not null                   comment '向量维度',
  chunk_count       int             not null                   comment '分块数（= 向量数）',
  vectors_digest    char(64)        not null                   comment '(vector_id, chunk_id, content_hash, required_perms) 列表的 SHA-256',
  built_time        datetime                                   comment '发布时间',
  primary key (id)
) engine=innodb comment = '智能助手知识库当前索引';
