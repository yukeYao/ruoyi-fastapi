---
doc_id: force-logout
title: 强制下线
version: 1
required_perms: []
sources:
  - backend/app/routers/monitor/online.py
  - backend/app/agent/write_tools.py
  - backend/app/services/online_service.py
---

## 作用

强制下线会让该用户的在线会话立即失效，需要重新登录。它不会停用账号，用户仍然可以马上重新登录。

## 权限与限制

- 需要 monitor:online:forceLogout 权限。
- 页面上在"系统监控 → 在线用户"中按会话强退。
- 通过智能助手操作时，按登录账号在服务端查找该用户的全部在线会话，并排除操作者自己当前的会话；
  需要审批，并校验目标在你的数据范围内；目标没有在线会话时提示"当前没有在线会话"。
