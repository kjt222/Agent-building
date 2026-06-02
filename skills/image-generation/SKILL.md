---
name: image-generation
description: Generate or edit images with the Image tool, including iterative refinement and consistency.
scope: image_generation
priority: 90
triggers:
  - "(?i)\\bimage generation\\b|\\bgenerate (?:an? )?image\\b|\\bmake (?:an? )?image\\b"
  - "(?i)\\bimg2img\\b|\\binpainting\\b|\\bimage edit\\b|\\bedit (?:this )?image\\b"
  - "(?i)\\bdigital human\\b|\\bavatar\\b"
  - "生图|生成图|生成图片|生成一张图|修图|改图|图像编辑|局部修改|数字人|形象|代言|风格一致|一组图"
tools:
  - Image
  - RenderDocument
---

For image-generation requests, use the Image tool to save concrete temporary
image artifacts. The UI displays generated images inline; do not present local
tmp/cache paths as the deliverable unless the user explicitly asks where the
cache file is. For small visual fixes, pass the previous output image or user
reference image back into Image as input/reference images instead of starting
over. For a series or recurring digital-human identity, keep using the
selected reference/output image paths to preserve identity, style, and
background consistency. For exact local text edits on an existing image, use
Image action='patch_text' with a pixel region after inspecting the target
area; this preserves pixels outside the region and avoids regenerating the
whole image.

Read Image result review metadata: if review.route is 'repair_required', do
not deliver yet; repair with patch_text or a narrower edit and then verify
again. If review.mode is 'semantic_not_run', use attached image feedback to
judge semantic requirements such as identity, pose, style, and background
before deciding whether another Image call is needed.
