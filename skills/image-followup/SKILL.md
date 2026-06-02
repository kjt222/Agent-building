---
name: image-followup
description: Follow-up edits on a previously generated image; only triggers when prior image context exists.
scope: image_generation
priority: 85
triggers:
  - "(?i)\\bimage\\b|\\bphoto\\b|\\bpicture\\b|\\bavatar\\b"
  - "(?i)\\beye(?:s)?\\b|\\bhair\\b|\\bface\\b|\\bbackground\\b|\\bcolor\\b"
  - "(?i)\\bchange\\b|\\bedit\\b|\\brevise\\b|\\bmake it\\b"
  - "图片|照片|这张|上一张|新图|数字人|形象|眼睛|眼睛|头发|脸|背景|颜色|蓝色|红色|绿色|黑色|白色|改|换|调整|修|重新生成|放哪|保存在哪"
history_triggers:
  - "tmp[/\\\\]generated_images|generated_image|rendered_image_path|image_path"
  - "\\.(?:png|jpg|jpeg|webp)\\b"
  - "生成(?:了)?(?:一张)?图|图片已保存|数字人"
tools:
  - Image
  - RenderDocument
---

The previous turn produced an image artifact. Treat this turn as a follow-up
edit. Reuse the prior output path or user reference path as input/reference
for Image; only start a brand-new generation if the user explicitly asked for
one. Prefer Image action='edit' for general fixes and action='patch_text' for
exact local text changes; both preserve identity better than a new generate.
