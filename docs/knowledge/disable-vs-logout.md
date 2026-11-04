---
doc_id: disable-vs-logout
title: 停用账号和强制下线有什么区别
version: 1
required_perms: []
sources:
  - backend/app/agent/tools.py
  - backend/app/agent/write_tools.py
---

## 区别

| 对比项 | 停用账号 | 强制下线 |
|---|---|---|
| 已登录的会话 | 保持在线 | 立即失效 |
| 之后能否登录 | 不能 | 能 |
| 需要的权限 | system:user:edit | monitor:online:forceLogout |

## 组合使用

要求"停用并下线"时，需要先停用、再强制下线，这是两个独立的操作，分别审批。
两者效果不同，不能互相代替：没有强制下线权限时，不能用停用账号代替下线，反之亦然。
