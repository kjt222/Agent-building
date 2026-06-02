---
name: local-kb-only
description: Strictly local knowledge-base lookups; no web access.
scope: knowledge
priority: 80
triggers:
  - "只根据知识库|仅根据知识库|只用知识库|仅用知识库|不要联网|不联网"
  - "(?i)local\\s+kb\\s+only|kb\\s+only|knowledge\\s+base\\s+only"
tools:
  - Read
  - Glob
  - Grep
  - KnowledgeSearch
---

The user explicitly requested a knowledge-base-only answer. Do not call
WebSearch or FetchURL. Use KnowledgeSearch and local file tools, then answer
based on what is found there. If KB evidence is missing, say so plainly
instead of falling back to model priors.
