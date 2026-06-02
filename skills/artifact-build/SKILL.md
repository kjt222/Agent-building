---
name: artifact-build
description: Code/UI/experiment artifact creation; full toolset including Verify, Bash, Job, Resource.
scope: artifact
priority: 50
triggers:
  - "(?i)\\bwrite\\b|\\bcreate\\b|\\bedit\\b|\\bmodify\\b|\\bfix\\b|\\bimplement\\b|\\bbuild\\b|\\brun\\b|\\btest\\b|\\brender\\b"
  - "(?i)\\bcode\\b|\\bhtml\\b|\\bcss\\b|\\bjavascript\\b|\\bpython\\b|\\bfile\\b|\\brepo\\b|\\bfrontend\\b|\\bsnake\\b|\\bgame\\b"
  - "(?i)\\bexperiment\\b|\\btraining\\b|\\btrain\\b|\\bsimulation\\b|\\bbenchmark\\b|\\bgpu\\b|\\bcuda\\b|\\bcpu\\b"
  - "实验|训练|仿真|模拟|跑一下|运行|显卡|算力"
  - "写|创建|修改|修复|实现|运行|测试|渲染|代码|文件|前端|复刻|贪吃蛇|游戏"
tools_base:
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - Bash
  - Job
  - Resource
tools:
  - KnowledgeSearch
  - KnowledgeIndex
  - Verify
  - RenderDocument
---

When you create or modify code, UI, documents, or other artifacts, first
create or modify the exact requested target. If the user gave an output path
and it does not exist, use Write to create it; do not substitute an older
similar file unless explicitly asked. Verification happens after the write:
read back written files, run targeted checks or smoke tests when practical,
and fix discovered issues before the final answer. For HTML, CSS, JavaScript,
browser UI, or game artifacts, call Verify with concrete browser assertions
after writing and reading the file, then fix any failed assertion.

For long experiments, training runs, simulations, benchmarks, or local
services, use Resource to inspect CPU/RAM/disk/GPU evidence, then use Bash
with background=true and monitor it with Job status/tail/cancel. After
completion, inspect logs and outputs before summarizing the result. Do not
ask the user to choose CPU or GPU unless the task itself requires a
scientific tradeoff; use the available resource evidence.
