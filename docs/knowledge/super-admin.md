---
doc_id: super-admin
title: 超级管理员的特殊规则
version: 1
required_perms: []
sources:
  - backend/app/core/constants.py
  - backend/app/services/user_service.py
  - backend/app/services/role_service.py
  - backend/app/core/data_scope.py
---

## 规则

- userId=1 的用户是超级管理员，拥有全部权限（权限字符 *:*:*），不受数据权限过滤。
- 超级管理员用户不允许修改、删除、停用，提示"不允许操作超级管理员用户"。
- roleId=1 的超级管理员角色不允许修改、删除、停用，提示"不允许操作超级管理员角色"。
- 智能助手也不能对超级管理员执行停用、解锁、强制下线。
