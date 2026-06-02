---
name: knowledge-lookup
description: Generic knowledge / document lookup against the local KB and files.
scope: knowledge
priority: 60
triggers:
  - "(?i)\\bknowledge\\b|\\bkb\\b|\\bsearch\\b|\\bpdf\\b|\\bdocument\\b|\\bdocx\\b|\\bxlsx\\b|\\bmemory\\b|\\bfact\\b"
  - "知识库|搜索|检索|资料|文档|记忆|事实"
tools:
  - Read
  - Glob
  - Grep
  - KnowledgeSearch
---

Use KnowledgeSearch first when the user is asking about indexed material;
fall back to Read/Glob/Grep for files outside the KB. Cite specific files or
KB ids when answering, and admit gaps instead of guessing.
