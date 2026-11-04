---
doc_id: data-scope-multi-role
title: 用户有多个角色时数据权限怎么计算
version: 1
required_perms: []
sources:
  - backend/app/core/data_scope.py
  - README.md
---

## 规则

- 多个角色的数据范围取并集。
- 停用的角色不参与计算。
- 只有拥有当前操作所需权限的角色才参与计算。例如查询用户列表时，只看拥有 system:user:list 的角色的数据范围。
- 任一参与计算的角色是"全部数据权限"时，不做过滤。
- 没有任何角色参与计算时，什么数据都看不到。

## 例子

用户同时有角色 A（全部数据权限，只有角色管理菜单）和角色 B（本部门，有用户管理菜单），
查询用户列表时只按角色 B 计算，只能看到本部门的用户。
