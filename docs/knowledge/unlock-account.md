---
doc_id: unlock-account
title: 解除账号锁定
version: 1
required_perms: []
sources:
  - backend/app/routers/monitor/logininfor.py
  - backend/app/agent/write_tools.py
---

## 在页面上解锁

在"系统监控 → 登录日志"中选择记录点击"解锁"，需要 monitor:logininfor:unlock 权限，作用是清除该账号的密码错误次数。

## 让智能助手解锁

需要 monitor:logininfor:unlock 权限，并经过审批。与页面不同，智能助手还会校验目标用户在你的数据范围内，
不能对自己或超级管理员操作。账号没有密码错误记录时会提示"没有密码错误记录，无需解锁"。

解锁不会修改账号的启用/停用状态；账号被停用时，解锁后仍然无法登录。
