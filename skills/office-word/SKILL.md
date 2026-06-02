---
name: office-word
description: Edit .docx Word documents with structured Read/Edit + Word-runtime structural ops + visual render verification.
scope: office_word
priority: 100
triggers:
  - "(?i)\\bword\\b|\\bdocx\\b|word document|document formatting"
  - "文档格式|修改文档|段落|标题|图注|正文|目录|章节"
tools_base:
  - Read
  - Glob
tools:
  - WordRead
  - WordEdit
  - WordRuntimeEdit
  - RenderDocument
---

Two edit tools. Pick by what the change touches:

- **WordEdit** (XML, fast): plain text inside one paragraph; no TOC / heading
  level / field / cross-ref / numbering / header-footer change. Cheap, no Word
  runtime needed.
- **WordRuntimeEdit** (real Word engine via COM): anything that touches TOC,
  heading boundaries / heading text, fields ({{ TOC }}, {{ PAGE }}, {{ REF }},
  {{ SEQ }}), cross-references, list numbering, headers / footers, page
  numbers. The runtime maintains TOC field cache, numbering, and styles
  consistency that python-docx mutation cannot guarantee. Use this whenever
  the user says "目录", "章节", "标题", "页码", "更新引用" or asks for a new
  section / heading.

Always WordRead first to learn the existing structure (heading texts, TOC
state). Anchors for WordRuntimeEdit are heading text, never paragraph_index;
structural edits shift indexes.

After any structural edit, run WordRuntimeEdit op=refresh_fields once before
the final save so TOC and page-number fields are up to date, then
RenderDocument to confirm visually.
