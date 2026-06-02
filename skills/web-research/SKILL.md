---
name: web-research
description: Public web research with KB cross-reference; combines local KB, WebSearch, and FetchURL.
scope: research
priority: 70
triggers:
  - "(?i)\\bweb\\b|\\binternet\\b|\\bonline\\b|search the web|look up|\\bsource\\b|\\bcitation\\b|\\blatest\\b|\\bcurrent\\b|\\brecent\\b|\\bdefinition\\b"
  - "上网|联网|网上|网页|查一下|搜一下|搜索一下|最新|来源|引用|定义"
  - "(?i)\\bGPT[- ]?(?:image|[0-9])|\\bClaude\\b|\\bGemini\\b|\\bDeepSeek\\b|\\bDoubao\\b|OpenAI\\s+API"
  - "豆包|火山引擎|模型可用性|图像生成模型"
tools:
  - KnowledgeSearch
  - WebSearch
  - FetchURL
---

For research questions that ask for online search, current facts, external
sources, citations, or definitions, combine available evidence deliberately.
If the same conversation already contains fresh source-backed evidence that
answers the user's follow-up, reuse that evidence instead of browsing again.
Use KnowledgeSearch for active local KBs when relevant, WebSearch to find
public sources, and FetchURL to inspect promising pages before relying on
them. Treat a source as official only when the domain belongs to the named
vendor or an explicitly affiliated platform; do not treat lookalike product
domains as official. In the final answer, keep local KB evidence, web
evidence, and your own synthesis distinct; when both local KB and web
evidence were used, include concise sections or labels for 本地知识库,
网页来源, and 结论. Resolve conflicts with a clear judgement instead of
averaging weak claims together.
