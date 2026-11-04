---
doc_id: agent-action-status
title: 审批卡片上各状态的含义
version: 1
required_perms: []
sources:
  - backend/app/core/constants.py
  - backend/app/services/agent_action_service.py
  - backend/app/services/agent_service.py
---

## 状态

| 状态 | 含义 |
|---|---|
| PENDING 待审批 | 尚未执行，等待用户批准或拒绝 |
| APPROVED 已批准 | 用户已批准，即将执行 |
| REJECTED 已拒绝 | 用户拒绝，没有执行 |
| EXPIRED 已过期 | 超过 10 分钟未处理，没有执行 |
| EXECUTING 执行中 | 已开始执行；长时间停在这里说明执行过程中断，结果未知 |
| SUCCEEDED 已成功 | 已执行成功 |
| FAILED 执行失败 | 执行时校验未通过或出错，数据未按预期修改 |

页面显示的状态取自服务端保存的审批记录，不取决于助手的回复内容。

## 特殊情况

- 停在"执行中"的操作不会自动重试，因为写操作可能已经生效，重试可能造成二次修改；需要人工到对应页面确认实际状态。
- 提示"操作已执行成功，但回复生成失败"时，说明操作本身已经完成，只是之后的总结没有生成，不需要重新批准。
