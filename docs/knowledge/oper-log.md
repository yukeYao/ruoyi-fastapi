---
doc_id: oper-log
title: 操作日志与智能助手的操作记录
version: 1
required_perms: [monitor:operlog:list]
sources:
  - backend/app/agent/approval.py
  - backend/app/core/oper_log.py
  - backend/app/routers/monitor/operlog.py
---

## 记录什么

新增、修改、删除等操作会写入操作日志，可以在"系统监控 → 操作日志"中查看，需要 monitor:operlog:list 权限。

## 智能助手的操作

- 智能助手的写操作在批准后真正执行时才写日志，模块标题与对应页面一致（用户管理、账户解锁、在线用户），
  方法字段以"智能助手:"开头标记来源，操作人是实际批准执行的用户。
- 批准后执行失败的操作也会写入，状态为异常。
- 审批请求本身、被拒绝和过期的操作不写入操作日志，它们只记录在审批记录里。
- 通过智能助手查询操作日志时只返回摘要，不返回请求参数和返回结果原文。
