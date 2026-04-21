---
name: spec-interview-doc
description: 通过 AskUserQuestionTool 进行深入、探究式访谈，将草稿规格说明（包括 plan.md 或用户指定文档）扩展为完整技术文档。适用于需要把 spec/设计稿/PRD/需求草稿完善为覆盖范围、UX、架构、数据、权衡、风险与验收标准的详细技术规格时。
---

# Spec Interview Doc

## Overview

读取草稿规格说明并进行深度访谈，最终输出完整技术规格并写回到原文件。

## Workflow

### 1. Identify the source document

- 如果用户提供文档路径，直接使用。
- 否则在工作区根目录查找 `plan.md`。
- 如果找不到，使用 AskUserQuestionTool 询问正确路径或创建新文件的位置。

### 2. Read and summarize the draft

- 提取目标、范围、关键约束和已确定的决策。
- 在访谈前列出缺失信息与开放问题清单。

### 3. Conduct the interview

- 每个问题都使用 AskUserQuestionTool。
- 提问要深入、非显而易见，能挖出约束、权衡与风险。
- 持续提问，直到用户确认访谈结束且关键缺口全部补齐。

### 4. Draft the final spec

- 使用下方模板补全完整规格说明。
- 明确标注假设，并在专门章节保留开放问题。
- 如果用户要求不同结构，按用户偏好调整。

### 5. Write the spec to the file

- 默认覆盖原文件；若用户要求新文件，则按其指定路径写入。
- 在回应中确认最终文件路径。

## Interview Guidance (Depth First)

采用层层深入的问题挖掘核心约束与权衡，避免仅复述草稿内容。深度问题示例：

- 为什么现在要做？有哪些替代方案被否决？原因是什么？
- 可衡量的成功指标或验收标准是什么？
- 需要哪些数据？数据来源、质量校验方式是什么？
- 失败路径、重试机制、部分完成如何处理？
- 性能、隐私、合规约束有哪些？
- 运行维护责任归属与监控要求是什么？
- 哪些非主流程的 UX 边界场景最关键？

维护开放问题清单，确认问题是否全部关闭。

## Spec Template (Default)

除非用户要求其他格式，默认使用以下结构：

1. Summary
2. Goals
3. Non-Goals
4. Scope and Requirements
5. Users and UX
6. Architecture Overview
7. Data Model and Storage
8. Integrations and APIs
9. Security and Privacy
10. Performance and Reliability
11. Rollout and Migration
12. Testing and Validation
13. Risks and Mitigations
14. Open Questions

## Output Rules

- 规格说明保持简洁可执行。
- 使用用户相同语言。
- 明确标注假设。
- 必须把最终规格写入指定文件。
