---
doc_id: role-menu-perm
title: 角色、菜单和权限字符的关系
version: 1
required_perms: []
sources:
  - backend/app/services/menu_service.py
  - backend/app/services/login_service.py
  - backend/app/core/constants.py
---

## 菜单类型

| 类型 | 含义 |
|---|---|
| M 目录 | 左侧菜单的分组 |
| C 菜单 | 一个页面，例如"智能助手" |
| F 按钮 | 页面里的操作按钮，只用于授权，不显示在左侧菜单 |

菜单和按钮可以带权限字符，格式为"模块:功能:操作"，例如 system:user:edit、agent:chat:use。

## 授权方式

权限通过"角色 ↔ 菜单"授予：给角色勾选菜单或按钮，拥有该角色的用户就获得其中的权限字符。
停用的角色和停用的菜单都不会带来权限。用户的权限是其所有正常角色的权限之和。
