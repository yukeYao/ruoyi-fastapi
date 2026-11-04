---
doc_id: scope-uncertain
title: 为什么提示"不存在或不在你的数据范围内"
version: 1
required_perms: []
sources:
  - backend/app/agent/write_tools.py
  - backend/app/agent/serialization.py
---

## 含义

查询结果为空或操作目标找不到时，可能是数据确实不存在，也可能是数据存在但不在你的数据范围内。
系统对两种情况返回相同的提示，避免通过不同的报错推断出范围外的数据是否存在。

## 怎么处理

- 这句提示不能理解为"一定不存在"。
- 确认账号拼写无误后，如果仍需要操作，请联系数据范围更大的管理员。
- 知识库检索没有结果时同样如此：没有找到相关说明，不代表系统没有这条规则。
