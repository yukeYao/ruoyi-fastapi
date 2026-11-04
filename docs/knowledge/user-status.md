---
doc_id: user-status
title: 停用和启用账号的影响
version: 1
required_perms: []
sources:
  - backend/app/routers/system/user.py
  - backend/app/agent/write_tools.py
  - backend/app/services/login_service.py
---

## 影响

- 停用后该账号不能再登录，登录提示"用户已封禁，请联系管理员"。
- 停用不会让已经登录的会话下线；需要立即下线时，另外执行强制下线。
- 启用后账号可以重新登录。

## 权限与限制

- 修改用户状态需要 system:user:edit 权限，目标用户必须在你的数据范围内。
- 不允许停用超级管理员（提示"不允许操作超级管理员用户"）。
- 通过智能助手操作时，不能停用自己；目标已经是该状态时提示无需操作；操作需要审批，审批预览会写明该账号当前的在线会话数。
