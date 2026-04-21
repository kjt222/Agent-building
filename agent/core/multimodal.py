"""Multimodal Message Builder - 多模态消息构建"""

from typing import List, Dict, Any, Optional, Union


class MultimodalMessageBuilder:
    """
    多模态消息构建器

    根据不同的 LLM 提供商构建正确格式的多模态消息。

    支持的格式：
    - OpenAI/GPT: image_url with base64 data URL
    - Anthropic/Claude: image source with base64
    - Zhipu: image_url (same as OpenAI)
    """

    @staticmethod
    def build_image_content(
        base64_data: str,
        media_type: str,
        provider: str = "openai"
    ) -> Dict[str, Any]:
        """
        构建图片内容块

        Args:
            base64_data: 图片的 base64 编码
            media_type: MIME 类型，如 "image/png"
            provider: LLM 提供商 ("openai", "anthropic", "zhipu", "gemini")

        Returns:
            适合该提供商的图片内容块
        """
        if provider in ("openai", "zhipu", "deepseek"):
            # OpenAI 格式: data URL
            return {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{media_type};base64,{base64_data}"
                }
            }
        elif provider == "anthropic":
            # Anthropic/Claude 格式
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64_data
                }
            }
        elif provider == "gemini":
            # Gemini 格式
            return {
                "type": "inline_data",
                "inline_data": {
                    "mime_type": media_type,
                    "data": base64_data
                }
            }
        else:
            # 默认使用 OpenAI 格式
            return {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{media_type};base64,{base64_data}"
                }
            }

    @staticmethod
    def build_text_content(text: str) -> Dict[str, Any]:
        """构建文本内容块"""
        return {
            "type": "text",
            "text": text
        }

    @classmethod
    def build_multimodal_content(
        cls,
        text: str,
        images: List[Dict[str, str]] = None,
        provider: str = "openai"
    ) -> Union[str, List[Dict[str, Any]]]:
        """
        构建多模态内容

        Args:
            text: 文本内容
            images: 图片列表，每个元素为 {"base64": "...", "media_type": "image/png"}
            provider: LLM 提供商

        Returns:
            如果没有图片，返回纯文本字符串
            如果有图片，返回内容块列表
        """
        if not images:
            return text

        content = []

        # 添加文本
        if text:
            content.append(cls.build_text_content(text))

        # 添加图片
        for img in images:
            content.append(cls.build_image_content(
                base64_data=img["base64"],
                media_type=img.get("media_type", "image/png"),
                provider=provider
            ))

        return content

    @classmethod
    def build_message_with_images(
        cls,
        role: str,
        text: str,
        images: List[Dict[str, str]] = None,
        provider: str = "openai"
    ) -> Dict[str, Any]:
        """
        构建带图片的消息

        Args:
            role: 消息角色 ("user", "assistant", "tool")
            text: 文本内容
            images: 图片列表
            provider: LLM 提供商

        Returns:
            完整的消息字典
        """
        content = cls.build_multimodal_content(text, images, provider)
        return {
            "role": role,
            "content": content
        }


def extract_images_from_tool_result(result_data: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """
    从工具结果中提取图片数据

    支持的类型：
    - type="image": read_image 工具返回
    - type="rendered_pdf_page": render_pdf_page 工具返回
    - type="pdf_images": extract_pdf_images 工具返回（提取第一张）

    Args:
        result_data: ToolResult.data

    Returns:
        如果包含图片，返回 {"base64": "...", "media_type": "..."}
        否则返回 None
    """
    if not isinstance(result_data, dict):
        return None

    data_type = result_data.get("type", "")

    # 直接图片类型 (read_image)
    if data_type == "image" and result_data.get("base64"):
        return {
            "base64": result_data["base64"],
            "media_type": result_data.get("media_type", "image/png")
        }

    # PDF 渲染页面 (render_pdf_page)
    if data_type == "rendered_pdf_page" and result_data.get("base64"):
        return {
            "base64": result_data["base64"],
            "media_type": result_data.get("media_type", "image/png")
        }

    # PDF 提取的图片列表 (extract_pdf_images) - 取第一张
    if data_type == "pdf_images":
        images = result_data.get("images", [])
        if images and isinstance(images, list) and len(images) > 0:
            first_img = images[0]
            if first_img.get("base64"):
                return {
                    "base64": first_img["base64"],
                    "media_type": first_img.get("media_type", "image/png")
                }

    return None


def convert_tool_result_to_message(
    tool_call_id: str,
    result: Any,
    provider: str = "openai"
) -> Dict[str, Any]:
    """
    将工具结果转换为消息

    如果结果包含图片，在消息中添加特殊标记，供 inject_images_into_conversation 处理。
    OpenAI API 的 tool 消息 content 必须是字符串，图片需要通过后续的 user 消息发送。

    Args:
        tool_call_id: 工具调用 ID
        result: ToolResult 对象
        provider: LLM 提供商

    Returns:
        消息字典
    """
    import json

    # 获取结果数据
    if hasattr(result, 'to_dict'):
        result_dict = result.to_dict()
    else:
        result_dict = result

    data = result_dict.get("data", {})

    # 检查是否有图片
    image_info = extract_images_from_tool_result(data)

    if image_info:
        # 有图片 - 保留 base64 数据，添加特殊标记供后续处理
        # inject_images_into_conversation 会提取这些数据并注入到 user 消息中

        # 根据不同的数据类型获取文件信息
        data_type = data.get("type", "image")
        file_path = data.get("file_path")
        file_name = data.get("file_name")

        # 对于 rendered_pdf_page，生成有意义的文件名
        if data_type == "rendered_pdf_page":
            page = data.get("page", 1)
            file_name = f"page_{page}.png"

        # 构建结果（包含 base64 数据和特殊标记）
        text_result = {
            "success": result_dict.get("success"),
            "type": "image",
            "original_type": data_type,
            "file_path": file_path,
            "file_name": file_name,
            "media_type": data.get("media_type"),
            "width": data.get("width"),
            "height": data.get("height"),
            # 特殊标记，供 inject_images_into_conversation 识别和提取
            "_has_image": True,
            "_image_base64": image_info["base64"],
            "_image_media_type": image_info["media_type"],
        }

        # 对于 PDF 渲染，添加页码信息
        if data_type == "rendered_pdf_page":
            text_result["page"] = data.get("page")
            text_result["total_pages"] = data.get("total_pages")

        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": json.dumps(text_result, ensure_ascii=False)
        }
    else:
        # 无图片 - 普通文本消息
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": json.dumps(result_dict, ensure_ascii=False)
        }


def inject_images_into_conversation(
    conversation: List[Dict[str, Any]],
    provider: str = "openai"
) -> List[Dict[str, Any]]:
    """
    扫描对话历史，将 tool 消息中的图片提取出来，注入到后续的 user 消息中

    这是因为大多数 API 的 tool 消息不支持直接发送图片，
    需要在 user 消息中发送图片。

    Args:
        conversation: 对话历史
        provider: LLM 提供商

    Returns:
        处理后的对话历史
    """
    import json

    new_conversation = []
    pending_images = []

    for msg in conversation:
        role = msg.get("role")
        content = msg.get("content", "")

        if role == "tool":
            # 检查 tool 消息是否包含图片
            try:
                if isinstance(content, str):
                    data = json.loads(content)
                    if data.get("_has_image") and data.get("_image_base64"):
                        # 收集图片，稍后注入
                        pending_images.append({
                            "base64": data["_image_base64"],
                            "media_type": data.get("_image_media_type", "image/png"),
                            "file_name": data.get("file_name", "image")
                        })
                        # 移除内部标记，保留其他信息
                        clean_data = {k: v for k, v in data.items() if not k.startswith("_")}
                        msg = {
                            "role": "tool",
                            "tool_call_id": msg.get("tool_call_id"),
                            "content": json.dumps(clean_data, ensure_ascii=False)
                        }
            except (json.JSONDecodeError, TypeError):
                pass

            new_conversation.append(msg)

        elif role == "user" and pending_images:
            # 将待处理的图片注入到 user 消息中
            builder = MultimodalMessageBuilder()
            text = content if isinstance(content, str) else str(content)

            # 添加图片说明
            image_desc = "\n\n[以下是工具返回的图片]"
            for img in pending_images:
                image_desc += f"\n- {img.get('file_name', 'image')}"

            multimodal_content = builder.build_multimodal_content(
                text=text + image_desc,
                images=pending_images,
                provider=provider
            )

            new_conversation.append({
                "role": "user",
                "content": multimodal_content
            })

            pending_images = []  # 清空

        else:
            new_conversation.append(msg)

    # 如果还有未处理的图片（在 tool 消息之后，没有后续 user 消息）
    # 需要添加一个 user 消息来携带这些图片，让模型能够"看到"图片内容
    #
    # 消息顺序：assistant (tool_calls) → tool → user (with images) → assistant
    # 这是正确的做法，GPT-5.2 等多模态模型支持在 user 消息中接收图片
    if pending_images:
        import logging
        logger = logging.getLogger("agent.multimodal")
        logger.info(f"Adding user message with {len(pending_images)} images after tool message")

        # 构建图片说明文本
        image_desc = "请查看以下工具返回的图片内容："
        for img in pending_images:
            image_desc += f"\n- {img.get('file_name', 'image')}"

        # 构建多模态 user 消息
        builder = MultimodalMessageBuilder()
        multimodal_content = builder.build_multimodal_content(
            text=image_desc,
            images=pending_images,
            provider=provider
        )

        new_conversation.append({
            "role": "user",
            "content": multimodal_content
        })

    return new_conversation
