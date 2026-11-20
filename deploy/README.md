# 若依 FastAPI 单机部署

在仓库根目录执行以下 Compose 命令。部署文件基于合入智能助手和知识库的 `master` 提交 `0bb9034`，需要该提交或包含它的版本。

## 本地启动与验收

1. 安装并启动 Docker，确认 `docker compose version` 可用。将根目录的 `.env.example` 复制为 `.env`，填入 `MYSQL_ROOT_PASSWORD`、`MYSQL_PASSWORD` 和 `JWT_SECRET`。三个值分别生成，例如在 Linux 上运行 `openssl rand -hex 32`。默认入口为 `http://127.0.0.1:8088`。
2. 构建镜像并启动服务。首次启动 MySQL 时会依次导入若依、Agent、知识库三份 SQL。后续启动复用原来的数据卷。

   ```sh
   docker compose config --quiet
   docker compose build api web
   docker compose up -d --wait
   docker compose ps
   ```

3. 在浏览器里验证验证码、登录、菜单、用户列表、页面内刷新和退出。种子管理员可用于首次登录。新建一条合成演示数据，然后执行 `docker compose down` 和 `docker compose up -d --wait`，重新登录确认数据仍在。

登录或请求失败时，先运行 `docker compose logs --tail=100 web api mysql redis`。`/prod-api/captchaImage` 应返回带业务 `code` 的 JSON。`docker compose down` 会保留数据卷。`docker compose down -v` 会删除数据库和助手数据。`backend/scripts/init_db.py` 会重建表并删除助手运行时文件，线上更新直接复用数据卷。

## 上云并启用 HTTPS

1. 在有 Docker Compose 的 Linux 云服务器上检出准备好的发布版本，复制 `.env.example` 为 `.env`，设置独立密钥并运行 `chmod 600 .env`。先用默认的回环地址启动服务。通过 SSH 隧道访问登录页。

   ```sh
   ssh -L 8088:127.0.0.1:8088 user@server
   ```

2. 打开 `http://127.0.0.1:8088`，在个人中心修改 `admin` 的种子密码，在用户管理停用或改密 `ry`，退出后用新密码重新登录。演示账号按所需功能分配权限。
3. 将域名解析到服务器，在防火墙和云安全组开放 Web 的 80/443，保留自己的 SSH 管理入口。在 `.env` 中将 `SITE_ADDRESS` 写成实际域名，将 `BIND_ADDRESS` 改为 `0.0.0.0`，将 `HTTP_PORT`、`HTTPS_PORT` 分别改为 `80`、`443`。执行 `docker compose up -d --wait web` 后从外网访问 HTTPS。Caddy 会在 DNS 和端口就绪后签发证书，证书存入持久化卷。FastAPI、MySQL 和 Redis 使用容器内网。

## 发布与回滚

首次正式构建前将 `.env` 里的 `RELEASE_TAG` 设置为发布提交的短 SHA，并记录该值。发布更新时，选一个数据库结构兼容的提交，等现有 GitHub Actions 测试通过，把 `RELEASE_TAG` 改为新提交的短 SHA，再执行：

```sh
docker compose build api web
docker compose up -d --wait --no-build
```

复测登录、用户列表和演示数据。回滚时将 `RELEASE_TAG` 写回上一版的值，再运行 `docker compose up -d --wait --no-build`。保留两版应用镜像，发布与回滚均复用同一 Compose 项目及其数据卷。

## 智能助手与知识库

在 `.env` 中设置 `AGENT_MODEL`、`AGENT_API_KEY` 和所需的 `AGENT_BASE_URL`。启用知识库时再设置对应的 `AGENT_EMBEDDING_*` 变量，然后启动 API 并构建索引。

```sh
docker compose up -d --wait api
docker compose exec api python scripts/build_knowledge_index.py
```

构建使用镜像中的 `docs/knowledge`，SQLite 会话和 FAISS 索引保存在 `api-data` 卷。用演示账号测试一条带来源的知识问答，再测试一次需要人工审批的操作。构建时文档和检索时的提问会发往配置的 Embedding 服务，演示使用合成数据。
