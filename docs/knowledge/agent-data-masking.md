---
doc_id: agent-data-masking
title: 智能助手返回数据的脱敏和不返回的字段
version: 1
required_perms: []
sources:
  - backend/app/agent/serialization.py
  - backend/app/agent/tools.py
---

## 脱敏

- 手机号保留前 3 位和后 4 位，例如 138****1234。
- 邮箱只保留用户名首字符和域名，例如 r***@qq.com。

## 不返回的内容

- 密码（含哈希）、登录令牌、会话 tokenId 一律不返回。
- 操作日志只返回摘要（模块、操作人、地址、状态、时间等），不返回请求参数和返回结果原文。
- 在线会话不返回 tokenId；强制下线由服务端按登录账号查找会话。
- 每个查询工具只返回声明过的字段，数据库新增的字段不会自动出现在结果里。

需要完整手机号、邮箱时，请到用户管理页面按权限查看。
