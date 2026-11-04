---
doc_id: relogin-after-change
title: 修改角色、权限或数据范围后为什么要重新登录
version: 1
required_perms: []
sources:
  - backend/app/services/login_service.py
  - backend/app/core/token_service.py
  - backend/app/core/constants.py
  - README.md
---

## 原因

用户登录时，系统把他的角色、权限字符、每个角色的数据范围和状态写入 Redis 中的登录态，之后的接口校验都读取这份登录态。
所以修改角色的菜单权限、数据权限、角色状态，或者给用户分配、取消角色后，对已经登录的会话通常不会立即生效，
需要对应用户退出后重新登录。

## 登录态有效期

登录态有效期为 30 分钟；剩余有效期不足 20 分钟时，访问接口会自动续期。

## 回答相关问题时

数据库里的配置是"重新登录后会得到的权限"，不能据此断言用户当前会话已经生效。
停用账号也不会让已登录的会话下线，需要的话另外执行强制下线。
