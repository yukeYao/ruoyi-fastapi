---
doc_id: online-sessions
title: 在线用户
version: 1
required_perms: [monitor:online:list]
sources:
  - backend/app/services/online_service.py
  - backend/app/agent/tools.py
---

## 内容

在线用户来自 Redis 中仍然有效的登录态，没有单独的表。在"系统监控 → 在线用户"中查看，需要 monitor:online:list 权限，
可以按账号、IP 筛选。显示账号、部门、IP、登录地点、浏览器、操作系统和登录时间。

## 通过智能助手查询

结果不包含会话 tokenId。一个账号可以有多个在线会话（例如多个浏览器）。
登录态过期（30 分钟内没有访问）后，会话会从在线列表中消失。
