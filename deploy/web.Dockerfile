FROM node:24-bookworm-slim AS build

WORKDIR /src
# 上游前端未提交 npm 锁文件，构建从已提交的依赖声明安装。
COPY ruoyi-ui/package.json ./
RUN npm install --no-audit --no-fund
COPY ruoyi-ui/ ./
RUN npm run build:prod

FROM caddy:2.11-alpine
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /src/dist /srv
