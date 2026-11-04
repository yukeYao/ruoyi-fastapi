---
doc_id: password-lock
title: 密码错误锁定规则
version: 1
required_perms: []
sources:
  - backend/app/services/login_service.py
  - backend/app/core/constants.py
---

## 规则

- 密码输错会累计错误次数，记录在 Redis 中；每次输错后有效期重新计为 10 分钟。
- 错误次数达到 5 次后账号被锁定，期间即使密码正确也会提示"密码输入错误5次，帐户锁定10分钟"。
- 错误次数到期后自动清除，锁定随之解除；登录成功也会清除错误次数。

## 提前解除

有 monitor:logininfor:unlock 权限的管理员可以清除错误次数，见"解除账号锁定"。
有 monitor:logininfor:list 权限时，可以让智能助手查询某个账号当前的错误次数和锁定剩余时间。
