---
doc_id: agent-capability-perms
title: 智能助手各能力对应的权限
version: 1
required_perms: []
sources:
  - backend/app/agent/tools.py
---

## 对照表

使用智能助手本身需要 agent:chat:use。每项能力还需要下表中的权限，没有权限时该能力不会提供给助手，硬调用也会被拒绝。

| 能力 | 工具 | 需要的权限 |
|---|---|---|
| 查询用户 | search_users | system:user:list |
| 查看用户详情 | get_user_detail | system:user:query |
| 查询角色 | list_roles | system:role:list |
| 查询部门 | list_departments | system:dept:list |
| 查询登录日志 | search_login_logs | monitor:logininfor:list |
| 统计登录失败 | top_failed_logins | monitor:logininfor:list |
| 查询账号锁定状态 | get_login_lock_status | monitor:logininfor:list |
| 查询操作日志 | search_oper_logs | monitor:operlog:list |
| 查询在线会话 | list_online_sessions | monitor:online:list |
| 启用/停用用户账号 | change_user_status | system:user:edit |
| 解除账号锁定 | unlock_user_account | monitor:logininfor:unlock |
| 强制下线 | force_logout_user | monitor:online:forceLogout |

## 说明

- 写操作（最后三项）即使有权限，也要经过审批才执行。
- 用户、角色、部门的查询结果还受数据权限限制。
- 缺少权限时，助手会说明缺少哪个权限并建议联系管理员授权，不会改用其他操作代替。
