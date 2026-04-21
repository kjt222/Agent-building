---
name: code-logic-review
description: "三层代码逻辑审查（Codex Review + Codex Exec + Claude 独立审查）。严格触发条件：仅在用户明确说"审查逻辑"、"审查代码"、"代码审查"、"逻辑审查"、"review code"时使用。写完代码后不要自动触发。"
user-invocable: true
allowed-tools: Bash, Read, Grep, Glob, Agent
argument-hint: "文件、模块目录、或功能描述"
---

# 代码逻辑审查

对指定的文件、模块目录或功能模块进行三层深度逻辑审查。

## 严格触发条件

**仅在用户明确说出以下关键词时使用此 skill**：
- "审查逻辑"、"审查代码"、"代码审查"、"逻辑审查"
- "review code"、"review logic"、"code review"
- "/code-logic-review"

**绝对不要**在以下情况自动触发：写完代码后、修完 bug 后、重构后、任何用户没有明确要求审查的场景。

## 参数解析

`$ARGUMENTS` 可以是：
- **单个文件**: `core/data/dataloaders.py`
- **模块目录**: `core/data/` 或 `rl/policies/`
- **功能描述**: `混合 batch 数据加载` 或 `multi-head 训练流程`
- **空**: 自动审查最近修改的文件（`git diff --name-only`）

如果是模块目录或功能描述，先用 Glob/Grep 找到所有相关文件，然后对整个模块做审查。

## 审查流程

### 步骤 0：确定审查范围

```
如果 $ARGUMENTS 是目录 → 列出目录下所有 .py 文件
如果 $ARGUMENTS 是功能描述 → grep/glob 定位相关文件
如果 $ARGUMENTS 为空 → git diff --name-only 获取最近改动文件
收集所有文件路径 → FILES
```

### 步骤 1+2：Codex Review + Codex Exec（并行执行）

**同时启动两个 Bash 命令**（后台运行）：

#### 1a. Codex Review（diff 审查）
```bash
# 如果有指定文件的未提交改动
git diff -- ${FILES} | codex review "审查这些代码改动，重点检查：逻辑正确性、边界情况、数据流一致性、接口契约。用中文回答。"
# 如果没有 diff，跳过此步
```

#### 1b. Codex Exec（深度逻辑审查）
```bash
codex exec --full-auto -s read-only "你是一个代码审查专家。深度审查以下模块的完整逻辑：

文件列表：${FILES}

审查要求：
1. 数据流追踪：追踪关键变量从创建到消费的完整链路，检查是否有断裂或类型变化
2. 边界情况：空值、None、空列表、长度不匹配、除零、溢出
3. 状态一致性：多个函数共享的状态是否在所有路径上保持一致
4. 接口契约：函数的输入输出是否符合所有调用者的预期
5. 梯度流（ML 代码）：梯度是否正确传播、detach/no_grad 是否合理
6. 隐藏耦合：是否依赖不明显的外部状态、全局变量、执行顺序
7. 并发安全：多线程/多进程下是否有竞态条件
8. 资源泄漏：文件句柄、GPU 内存、数据库连接是否正确释放

用中文回答。按严重程度分级（P0=必须修/P1=应该修/P2=建议改进）。
每个问题给出：文件名:行号 + 问题描述 + 修复建议。"
```

### 步骤 3：Claude 自身独立审查

在等待 Codex 结果的同时或之后，Claude 自身也要：

1. **读取**所有审查范围内的文件
2. **画出**关键数据流图（mentally）：从输入到输出的完整路径
3. **逐一检查**：
   - 类型安全：函数间传递的数据类型是否匹配（特别是 list vs tensor, str vs int）
   - 状态变异：是否有意外的 in-place 修改影响了调用者
   - 分支覆盖：if/else 是否覆盖了所有情况，是否有遗漏的 edge case
   - 错误处理：异常是否被正确 catch/propagate，是否有静默失败
   - 性能陷阱：O(n²) 循环、重复计算、不必要的拷贝
   - ML 特有：loss 计算是否正确、梯度流是否被意外截断、device 是否一致
4. **对比** Codex 发现，补充 Codex 遗漏的问题

### 输出格式

```markdown
## 代码逻辑审查报告

**审查范围**: [文件/模块列表]
**审查时间**: [日期]

### Codex Review 发现（diff 层面）
- [P0/P1/P2] 问题描述 — 文件:行号
  修复建议：...

### Codex Exec 发现（深度逻辑）
- [P0/P1/P2] 问题描述 — 文件:行号
  修复建议：...

### Claude 独立发现
- [P0/P1/P2] 问题描述 — 文件:行号
  修复建议：...

### 综合结论
| 级别 | 数量 | 说明 |
|------|------|------|
| P0 | N | 必须立即修复 |
| P1 | N | 应该修复 |
| P2 | N | 建议改进 |

**确认安全的方面**：[列出经过审查确认没有问题的关键逻辑]
```

## 注意事项

- 如果 codex 额度用完（报错 "usage limit"），跳过 codex 步骤，只做 Claude 自身审查，并告知用户
- codex exec 使用 `read-only` sandbox，不会修改任何文件
- 步骤 1a 和 1b 并行执行以节省时间
- 对模块级审查，关注模块内各文件的接口契约和数据流一致性，而不仅仅是单个文件内部
