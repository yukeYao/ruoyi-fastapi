---
doc_id: grant-agent-access
title: 如何给角色或用户开通智能助手
version: 1
required_perms: [system:role:edit]
sources:
  - backend/sql/agent.sql
  - backend/app/routers/system/role.py
  - backend/app/routers/system/user.py
---

## 给角色授权

1. 进入"系统管理 → 角色管理"，修改目标角色（需要 system:role:edit 权限）。
2. 在"菜单权限"中勾选"智能助手"，保存。
3. 确认角色状态为正常；停用的角色不会加载任何权限。

超级管理员角色（roleId=1）不允许修改，也不需要授权。

## 给用户分配角色

如果用户还没有已授权的角色，在"系统管理 → 用户管理"中为该用户分配角色（需要 system:user:edit 权限）。
分配角色同样受数据权限限制：只能给自己数据范围内的用户分配，且只能分配自己数据范围内的角色。

## 让授权生效

接口权限在登录时写入登录态，授权完成后需要让用户退出并重新登录。智能助手不能代替管理员执行授权操作。
