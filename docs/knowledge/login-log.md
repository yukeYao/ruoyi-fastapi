---
doc_id: login-log
title: 登录日志与登录失败统计
version: 1
required_perms: [monitor:logininfor:list]
sources:
  - backend/app/routers/monitor/logininfor.py
  - backend/app/agent/tools.py
---

## 内容

登录日志记录每次登录尝试的账号、IP、登录地点、浏览器、操作系统、状态（0 成功、1 失败）、提示信息和时间，
在"系统监控 → 登录日志"中查看，需要 monitor:logininfor:list 权限。

## 通过智能助手查询

- 可以按账号、IP、状态查询最近若干天（最多 30 天）的登录日志。
- 可以统计最近若干天登录失败次数最多的账号，以及每个账号失败涉及的 IP 数和最后失败时间。
- 登录地点只区分"内网IP"和"未知地点"，不查询真实地理位置。
