---
doc_id: login-captcha
title: 登录与验证码
version: 1
required_perms: []
sources:
  - backend/app/services/login_service.py
  - backend/app/services/captcha_service.py
  - backend/app/core/constants.py
---

## 验证码

是否启用验证码由系统参数 sys.account.captchaEnabled 控制（true 启用）。验证码有效期 2 分钟，
过期提示"验证码已失效"，填错提示"验证码错误"。

## 常见登录提示

| 提示 | 含义 |
|---|---|
| 用户不存在/密码错误 | 账号不存在或密码错误，两种情况提示相同 |
| 对不起，您的账号已被删除 | 账号已被逻辑删除 |
| 用户已封禁，请联系管理员 | 账号被停用 |
| 密码输入错误5次，帐户锁定10分钟 | 密码连续输错达到上限，见"密码错误锁定规则" |
