---
doc_id: password-lock
title: 密码错误锁定规则
version: 2
required_perms: []
sources:
  - backend/app/services/login_service.py
  - backend/app/core/constants.py
---

## 规则

- 每次密码输错，该账号的错误次数加 1，记录在 Redis 中；每次输错后，错误次数的有效期重新计为 10 分钟。
- 错误次数达到 5 次后账号被锁定。锁定期间无法登录：系统先检查错误次数，再校验密码，所以即使输入正确的密码，
  也会提示"密码输入错误5次，帐户锁定10分钟"。
- 错误次数在最后一次输错 10 分钟后自动清除，锁定随之解除，之后可以正常登录。
- 没有被锁定时（错误少于 5 次），登录成功会清除错误次数；锁定期间不可能登录成功，也就不会因此清除。

## 提前解除

有 monitor:logininfor:unlock 权限的管理员可以清除错误次数，见"解除账号锁定"。
有 monitor:logininfor:list 权限时，可以让智能助手查询某个账号当前的错误次数和锁定剩余时间。
