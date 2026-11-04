---
doc_id: unique-rules
title: 唯一性规则
version: 1
required_perms: []
sources:
  - backend/app/services/user_service.py
  - backend/app/services/role_service.py
  - backend/app/services/dept_service.py
  - backend/app/services/menu_service.py
---

## 规则

新增和修改时都会校验，修改时排除自身，已删除的数据不参与比较。

| 对象 | 必须唯一 |
|---|---|
| 用户 | 登录账号、手机号、邮箱 |
| 角色 | 角色名称、权限字符 |
| 部门 | 同一上级部门下的部门名称 |
| 菜单 | 同一上级下的菜单名称；路由地址和路由名称按若依规则校验 |

## 常见提示

- 新增用户失败，登录账号已存在 / 手机号码已存在 / 邮箱账号已存在。
- 新增角色失败，角色名称已存在 / 角色权限已存在。
- 新增部门失败，部门名称已存在。
