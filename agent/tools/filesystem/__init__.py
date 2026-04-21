"""File System Tools for Agent - 文件读取工具（支持多模态）

工具列表（5个，合并优化）：
- read_file: 读取文件（自动识别文本/图片）- 合并了原 read_image
- list_directory: 列出目录内容
- extract_pdf_images: 从 PDF 提取嵌入图片
- render_pdf_page: 渲染 PDF 页面为图片
- get_pdf_info: 获取 PDF 基本信息

工具设计原则 (参考 Anthropic 最佳实践):
- 清晰的使用场景说明
- 具体的错误提示和解决建议
- 功能整合，减少工具数量
"""

import base64
import mimetypes
from pathlib import Path
from typing import Callable, Optional

from ..base import Tool, ToolResult, ToolCategory, PermissionLevel
from ..registry import get_registry


# 支持的图片格式
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'}
IMAGE_MIME_TYPES = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.webp': 'image/webp',
    '.bmp': 'image/bmp',
}

# 支持的文本格式
TEXT_EXTENSIONS = {
    '.txt', '.md', '.py', '.js', '.ts', '.json', '.yaml', '.yml',
    '.html', '.css', '.xml', '.csv', '.log', '.sh', '.bat', '.ps1',
    '.java', '.c', '.cpp', '.h', '.hpp', '.go', '.rs', '.rb', '.php',
    '.sql', '.r', '.m', '.swift', '.kt', '.scala', '.lua', '.pl',
    '.ini', '.conf', '.cfg', '.toml', '.env', '.gitignore', '.dockerfile'
}

# 支持的文档格式
DOCUMENT_EXTENSIONS = {'.pdf', '.pptx', '.docx'}


def is_image_file(path: Path) -> bool:
    """判断是否为图片文件"""
    return path.suffix.lower() in IMAGE_EXTENSIONS


def is_text_file(path: Path) -> bool:
    """判断是否为文本文件"""
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTENSIONS:
        return True
    # 无后缀文件尝试作为文本读取
    if not suffix:
        return True
    return False


def read_image_as_base64(path: Path) -> tuple[str, str]:
    """
    读取图片并转为 base64

    Returns:
        (base64_data, media_type)
    """
    with open(path, 'rb') as f:
        data = f.read()

    base64_data = base64.standard_b64encode(data).decode('utf-8')
    media_type = IMAGE_MIME_TYPES.get(path.suffix.lower(), 'image/png')

    return base64_data, media_type


def create_filesystem_tools(
    allowed_paths: list[str] = None,
    max_file_size_mb: float = 10.0
) -> list[Tool]:
    """
    创建文件系统工具集

    Args:
        allowed_paths: 允许访问的路径列表（安全限制）
        max_file_size_mb: 最大文件大小限制（MB）

    Returns:
        工具列表 (5个工具，原6个合并)
    """

    max_file_size = int(max_file_size_mb * 1024 * 1024)

    def _check_path_allowed(path: Path) -> bool:
        """检查路径是否在允许范围内"""
        if not allowed_paths:
            return True  # 未配置限制时允许所有

        path_str = str(path.resolve())
        for allowed in allowed_paths:
            if path_str.startswith(str(Path(allowed).resolve())):
                return True
        return False

    # ============================================================
    # 工具 1: read_file - 统一的文件读取（合并了原 read_image）
    # ============================================================
    def read_file(file_path: str, encoding: str = "utf-8") -> ToolResult:
        """
        读取文件内容，自动识别文件类型。

        - 图片文件：返回 base64 编码（用于多模态分析）
        - 文本文件：返回文本内容
        - 其他文件：返回文件信息
        """
        try:
            path = Path(file_path)

            # 检查文件存在
            if not path.exists():
                return ToolResult(
                    success=False,
                    error=f"文件不存在: {file_path}",
                    data={
                        "suggestion": "请检查文件路径是否正确，可使用 list_directory 查看目录内容"
                    }
                )

            if not path.is_file():
                if path.is_dir():
                    return ToolResult(
                        success=False,
                        error=f"这是一个目录，不是文件: {file_path}",
                        data={
                            "suggestion": "使用 list_directory 查看目录内容",
                            "alternative_tool": "list_directory"
                        }
                    )
                return ToolResult(
                    success=False,
                    error=f"不是有效文件: {file_path}"
                )

            # 检查路径权限
            if not _check_path_allowed(path):
                return ToolResult(
                    success=False,
                    error=f"无权访问该路径: {file_path}",
                    data={
                        "suggestion": "该路径不在允许访问的范围内，请检查安全配置"
                    }
                )

            # 检查文件大小
            file_size = path.stat().st_size
            if file_size > max_file_size:
                return ToolResult(
                    success=False,
                    error=f"文件过大: {file_size / 1024 / 1024:.2f}MB，最大允许: {max_file_size_mb}MB",
                    data={
                        "current_size_mb": round(file_size / 1024 / 1024, 2),
                        "max_size_mb": max_file_size_mb,
                        "suggestion": "对于大文件，可以考虑：1) 分块读取 2) 压缩后上传 3) 仅读取部分内容"
                    }
                )

            # 图片文件 -> base64 (多模态)
            if is_image_file(path):
                base64_data, media_type = read_image_as_base64(path)
                return ToolResult(
                    success=True,
                    data={
                        "type": "image",
                        "file_path": str(path.resolve()),
                        "file_name": path.name,
                        "media_type": media_type,
                        "base64": base64_data,
                        "size_bytes": file_size,
                        "description": "图片已加载，可以直接分析图片内容"
                    },
                    metadata={"is_multimodal": True}
                )

            # 文本文件 -> 内容
            if is_text_file(path):
                try:
                    content = path.read_text(encoding=encoding)
                    return ToolResult(
                        success=True,
                        data={
                            "type": "text",
                            "file_path": str(path.resolve()),
                            "file_name": path.name,
                            "content": content,
                            "size_bytes": file_size,
                            "line_count": content.count('\n') + 1
                        }
                    )
                except UnicodeDecodeError:
                    # 尝试其他编码
                    for alt_encoding in ['gbk', 'gb2312', 'latin-1']:
                        try:
                            content = path.read_text(encoding=alt_encoding)
                            return ToolResult(
                                success=True,
                                data={
                                    "type": "text",
                                    "file_path": str(path.resolve()),
                                    "file_name": path.name,
                                    "content": content,
                                    "size_bytes": file_size,
                                    "encoding_used": alt_encoding,
                                    "note": f"使用 {alt_encoding} 编码成功解码"
                                }
                            )
                        except UnicodeDecodeError:
                            continue
                    return ToolResult(
                        success=False,
                        error="无法解码文件，尝试了 UTF-8、GBK、GB2312、Latin-1 编码",
                        data={
                            "suggestion": "文件可能是二进制格式或使用了特殊编码",
                            "tried_encodings": ["utf-8", "gbk", "gb2312", "latin-1"]
                        }
                    )

            # PDF 文件特殊提示
            if path.suffix.lower() == '.pdf':
                return ToolResult(
                    success=True,
                    data={
                        "type": "pdf",
                        "file_path": str(path.resolve()),
                        "file_name": path.name,
                        "size_bytes": file_size,
                        "message": "这是 PDF 文件，无法直接读取内容。",
                        "available_tools": {
                            "get_pdf_info": "获取 PDF 基本信息（页数、作者等）",
                            "render_pdf_page": "将指定页面渲染为图片来查看",
                            "extract_pdf_images": "提取 PDF 中嵌入的图片"
                        }
                    }
                )

            # 其他文件 -> 返回信息
            mime_type, _ = mimetypes.guess_type(str(path))
            return ToolResult(
                success=True,
                data={
                    "type": "binary",
                    "file_path": str(path.resolve()),
                    "file_name": path.name,
                    "mime_type": mime_type or "application/octet-stream",
                    "size_bytes": file_size,
                    "message": "二进制文件，无法直接读取内容",
                    "suggestion": "如需处理请使用专门的工具或将其转换为可读格式"
                }
            )

        except PermissionError:
            return ToolResult(
                success=False,
                error=f"无权限读取文件: {file_path}",
                data={
                    "suggestion": "请检查文件权限，或尝试以管理员身份运行"
                }
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"读取文件失败: {str(e)}",
                data={
                    "suggestion": "这可能是临时问题，请稍后重试"
                }
            )

    # ============================================================
    # 工具 2: list_directory - 列出目录
    # ============================================================
    def list_directory(dir_path: str, pattern: str = "*") -> ToolResult:
        """
        列出目录内容，包括文件和子目录。
        """
        try:
            path = Path(dir_path)

            if not path.exists():
                return ToolResult(
                    success=False,
                    error=f"目录不存在: {dir_path}",
                    data={
                        "suggestion": "请检查路径是否正确"
                    }
                )

            if not path.is_dir():
                if path.is_file():
                    return ToolResult(
                        success=False,
                        error=f"这是一个文件，不是目录: {dir_path}",
                        data={
                            "suggestion": "使用 read_file 读取文件内容",
                            "alternative_tool": "read_file"
                        }
                    )
                return ToolResult(success=False, error=f"不是有效目录: {dir_path}")

            if not _check_path_allowed(path):
                return ToolResult(
                    success=False,
                    error=f"无权访问该路径: {dir_path}",
                    data={
                        "suggestion": "该路径不在允许访问的范围内"
                    }
                )

            files = []
            dirs = []

            for item in path.glob(pattern):
                if item.is_file():
                    files.append({
                        "name": item.name,
                        "size": item.stat().st_size,
                        "is_image": is_image_file(item),
                        "is_text": is_text_file(item)
                    })
                elif item.is_dir():
                    dirs.append({"name": item.name})

            result_data = {
                "path": str(path.resolve()),
                "directories": dirs,
                "files": files,
                "total_dirs": len(dirs),
                "total_files": len(files)
            }

            if not files and not dirs:
                result_data["message"] = "目录为空或没有匹配的文件"
                if pattern != "*":
                    result_data["suggestion"] = f"没有匹配 '{pattern}' 的文件，尝试使用 '*' 列出所有文件"

            return ToolResult(success=True, data=result_data)

        except PermissionError:
            return ToolResult(
                success=False,
                error=f"无权限访问目录: {dir_path}",
                data={"suggestion": "请检查目录权限"}
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"列出目录失败: {str(e)}",
                data={"suggestion": "这可能是临时问题，请稍后重试"}
            )

    # ============================================================
    # 工具 3: extract_pdf_images - 提取 PDF 嵌入图片
    # ============================================================
    def extract_pdf_images(file_path: str, page_numbers: str = "") -> ToolResult:
        """
        从 PDF 文件中提取嵌入的位图图片。
        """
        try:
            path = Path(file_path)

            if not path.exists():
                return ToolResult(
                    success=False,
                    error=f"文件不存在: {file_path}",
                    data={"suggestion": "请检查文件路径"}
                )

            if not path.is_file():
                return ToolResult(success=False, error=f"不是文件: {file_path}")

            if path.suffix.lower() != '.pdf':
                return ToolResult(
                    success=False,
                    error=f"不是 PDF 文件: {file_path}",
                    data={
                        "file_extension": path.suffix,
                        "suggestion": "此工具仅支持 PDF 文件"
                    }
                )

            if not _check_path_allowed(path):
                return ToolResult(success=False, error=f"无权访问该路径: {file_path}")

            try:
                from ...rag.parsers import extract_pdf_images as _extract_pdf_images
            except ImportError:
                return ToolResult(
                    success=False,
                    error="PyMuPDF 未安装",
                    data={
                        "suggestion": "请运行: pip install pymupdf",
                        "package": "pymupdf"
                    }
                )

            images = _extract_pdf_images(path, output_dir=None)

            # 解析页码过滤
            target_pages = set()
            if page_numbers:
                try:
                    for part in page_numbers.split(','):
                        part = part.strip()
                        if '-' in part:
                            start, end = part.split('-', 1)
                            for p in range(int(start), int(end) + 1):
                                target_pages.add(p)
                        else:
                            target_pages.add(int(part))
                except ValueError:
                    return ToolResult(
                        success=False,
                        error=f"页码格式错误: '{page_numbers}'",
                        data={
                            "suggestion": "正确格式示例：'1,3,5-7' 表示第1、3、5、6、7页",
                            "examples": ["1", "1,3,5", "1-5", "1,3,5-10"]
                        }
                    )

            # 过滤图片
            if target_pages:
                images = [img for img in images if img['page'] in target_pages]

            if not images:
                return ToolResult(
                    success=True,
                    data={
                        "type": "pdf_images",
                        "file_path": str(path.resolve()),
                        "images": [],
                        "total_images": 0,
                        "message": "PDF 中没有找到嵌入的位图图片",
                        "suggestion": "如果 PDF 是由 PPT 导出或主要包含矢量图形，请改用 render_pdf_page 渲染页面"
                    }
                )

            return ToolResult(
                success=True,
                data={
                    "type": "pdf_images",
                    "file_path": str(path.resolve()),
                    "images": images,
                    "total_images": len(images)
                },
                metadata={"is_multimodal": True}
            )

        except RuntimeError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"提取 PDF 图片失败: {str(e)}",
                data={"suggestion": "请确保 PDF 文件未损坏"}
            )

    # ============================================================
    # 工具 4: render_pdf_page - 渲染 PDF 页面
    # ============================================================
    def render_pdf_page(file_path: str, page_number: int = 1, dpi: int = 150) -> ToolResult:
        """
        渲染 PDF 页面为图片（整页截图）并保存为临时文件。
        """
        try:
            path = Path(file_path)

            if not path.exists():
                return ToolResult(
                    success=False,
                    error=f"文件不存在: {file_path}",
                    data={"suggestion": "请检查文件路径"}
                )

            if not path.is_file():
                return ToolResult(success=False, error=f"不是文件: {file_path}")

            if path.suffix.lower() != '.pdf':
                return ToolResult(
                    success=False,
                    error=f"不是 PDF 文件: {file_path}",
                    data={"suggestion": "此工具仅支持 PDF 文件"}
                )

            if not _check_path_allowed(path):
                return ToolResult(success=False, error=f"无权访问该路径: {file_path}")

            try:
                from ...rag.parsers import render_pdf_pages, get_pdf_info
            except ImportError:
                return ToolResult(
                    success=False,
                    error="PyMuPDF 未安装",
                    data={
                        "suggestion": "请运行: pip install pymupdf",
                        "package": "pymupdf"
                    }
                )

            # 获取 PDF 信息
            pdf_info = get_pdf_info(path)
            total_pages = pdf_info["total_pages"]

            if page_number < 1 or page_number > total_pages:
                return ToolResult(
                    success=False,
                    error=f"页码超出范围: 请求第 {page_number} 页，但 PDF 只有 {total_pages} 页",
                    data={
                        "requested_page": page_number,
                        "total_pages": total_pages,
                        "valid_range": f"1-{total_pages}",
                        "suggestion": f"请输入 1 到 {total_pages} 之间的页码"
                    }
                )

            # 渲染指定页面
            rendered = render_pdf_pages(path, page_numbers=[page_number], dpi=dpi)

            if not rendered:
                return ToolResult(success=False, error="渲染失败，请重试")

            page_data = rendered[0]

            # 保存为临时文件
            import tempfile
            temp_dir = Path(tempfile.gettempdir()) / "agent_pdf_renders"
            temp_dir.mkdir(exist_ok=True)

            pdf_name = path.stem
            safe_name = "".join(c if c.isalnum() or c in "._- " else "_" for c in pdf_name)[:50]
            temp_filename = f"{safe_name}_page_{page_number}.png"
            temp_path = temp_dir / temp_filename

            image_data = base64.b64decode(page_data["base64"])
            with open(temp_path, "wb") as f:
                f.write(image_data)

            return ToolResult(
                success=True,
                data={
                    "type": "rendered_pdf_page",
                    "source_pdf": str(path.resolve()),
                    "page": page_number,
                    "total_pages": total_pages,
                    "width": page_data["width"],
                    "height": page_data["height"],
                    "rendered_image_path": str(temp_path),
                    "message": f"PDF 第 {page_number}/{total_pages} 页已渲染。使用 read_file 读取 '{temp_path}' 来查看图片内容。"
                },
                metadata={"is_multimodal": False}
            )

        except RuntimeError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"渲染 PDF 页面失败: {str(e)}",
                data={"suggestion": "请确保 PDF 文件未损坏"}
            )

    # ============================================================
    # 工具 5: get_pdf_info - 获取 PDF 信息
    # ============================================================
    def get_pdf_info_tool(file_path: str) -> ToolResult:
        """
        获取 PDF 文件的基本信息。
        """
        try:
            path = Path(file_path)

            if not path.exists():
                return ToolResult(
                    success=False,
                    error=f"文件不存在: {file_path}",
                    data={"suggestion": "请检查文件路径"}
                )

            if path.suffix.lower() != '.pdf':
                return ToolResult(
                    success=False,
                    error=f"不是 PDF 文件: {file_path}",
                    data={"suggestion": "此工具仅支持 PDF 文件"}
                )

            if not _check_path_allowed(path):
                return ToolResult(success=False, error=f"无权访问该路径: {file_path}")

            try:
                from ...rag.parsers import get_pdf_info
            except ImportError:
                return ToolResult(
                    success=False,
                    error="PyMuPDF 未安装",
                    data={
                        "suggestion": "请运行: pip install pymupdf",
                        "package": "pymupdf"
                    }
                )

            info = get_pdf_info(path)

            return ToolResult(
                success=True,
                data={
                    "type": "pdf_info",
                    "file_path": str(path.resolve()),
                    "file_name": path.name,
                    "total_pages": info["total_pages"],
                    "title": info.get("title", ""),
                    "author": info.get("author", ""),
                    "available_actions": {
                        "render_pdf_page": f"渲染页面为图片（1-{info['total_pages']}）",
                        "extract_pdf_images": "提取嵌入的图片"
                    }
                }
            )

        except RuntimeError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"获取 PDF 信息失败: {str(e)}",
                data={"suggestion": "请确保 PDF 文件未损坏"}
            )

    # ============================================================
    # 工具描述（遵循 Anthropic 最佳实践）
    # ============================================================

    READ_FILE_DESCRIPTION = """读取文件内容，自动识别文件类型（文本、图片、PDF等）。

## 使用场景
- 用户要求"看看这个文件"、"读取xxx文件"
- 需要分析图片内容（截图、设计图、图表等）
- 读取代码文件、配置文件、日志等
- 查看文本文档内容

## 自动处理逻辑
- **图片文件**（PNG/JPG/GIF/WebP/BMP）：返回 base64，可直接分析图片内容
- **文本文件**（py/js/md/txt/json 等）：返回文本内容
- **PDF 文件**：提示使用专门的 PDF 工具
- **其他文件**：返回文件信息

## 不适用场景
- 列出目录内容 → 使用 list_directory
- PDF 页面渲染 → 使用 render_pdf_page
- PDF 图片提取 → 使用 extract_pdf_images

## 示例
- 读取图片：read_file("screenshot.png")
- 读取代码：read_file("main.py")
- 读取中文文件：read_file("文档.txt", encoding="gbk")"""

    LIST_DIRECTORY_DESCRIPTION = """列出目录内容，包括文件和子目录。

## 使用场景
- 用户询问"目录里有什么"、"列出文件"
- 查找特定类型的文件
- 了解项目结构

## 参数说明
- dir_path: 目录路径
- pattern: 通配符过滤（默认 "*"）
  - "*.py" 只列出 Python 文件
  - "*.jpg" 只列出 JPG 图片
  - "test_*" 列出以 test_ 开头的文件

## 不适用场景
- 读取文件内容 → 使用 read_file

## 示例
- 列出所有文件：list_directory("/path/to/dir")
- 只看 Python 文件：list_directory("/path", pattern="*.py")"""

    EXTRACT_PDF_IMAGES_DESCRIPTION = """从 PDF 文件中提取嵌入的位图图片。

## 使用场景
- PDF 中包含照片、截图等位图图片
- 需要单独获取 PDF 里的图片资源

## 不适用场景
- PDF 是 PPT 导出的（主要是矢量图形）→ 使用 render_pdf_page
- 想看 PDF 页面的完整布局 → 使用 render_pdf_page

## 参数说明
- file_path: PDF 文件路径
- page_numbers: 可选，指定页码（如 "1,3,5-7"）

## 注意
此工具提取的是 PDF 中**嵌入的位图**，不是页面渲染。如果 PDF 内容主要是文字和矢量图形，可能提取不到图片。"""

    RENDER_PDF_PAGE_DESCRIPTION = """将 PDF 页面渲染成图片（整页截图）。

## 使用场景
- 查看 PDF 页面的完整视觉布局
- PDF 是 PPT/Word 导出的，需要看排版效果
- 分析 PDF 中的图表、表格等视觉元素

## 工作流程
1. 调用 render_pdf_page 渲染指定页面
2. 返回临时图片路径
3. 使用 read_file 读取该图片进行分析

## 参数说明
- file_path: PDF 文件路径
- page_number: 页码（从1开始，默认1）
- dpi: 分辨率（默认150，越高越清晰）

## 示例
render_pdf_page("document.pdf", page_number=1)
→ 返回临时图片路径
read_file(临时图片路径)
→ 分析页面内容"""

    GET_PDF_INFO_DESCRIPTION = """获取 PDF 文件的基本信息（页数、标题、作者）。

## 使用场景
- 在渲染前了解 PDF 有多少页
- 获取 PDF 的元数据

## 返回信息
- total_pages: 总页数
- title: 文档标题
- author: 作者

## 示例
用户："这个 PDF 有几页？"
→ get_pdf_info("document.pdf")
→ "PDF 共 10 页，标题：xxx，作者：xxx" """

    # ============================================================
    # 创建工具列表（5个工具）
    # ============================================================
    tools = [
        Tool(
            name="read_file",
            description=READ_FILE_DESCRIPTION,
            category=ToolCategory.FILE_SYSTEM,
            permission=PermissionLevel.AUTO,
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "文件的完整路径"
                    },
                    "encoding": {
                        "type": "string",
                        "description": "文本编码（默认 UTF-8，中文可能需要 GBK）",
                        "default": "utf-8"
                    }
                },
                "required": ["file_path"]
            },
            handler=read_file
        ),
        Tool(
            name="list_directory",
            description=LIST_DIRECTORY_DESCRIPTION,
            category=ToolCategory.FILE_SYSTEM,
            permission=PermissionLevel.AUTO,
            parameters={
                "type": "object",
                "properties": {
                    "dir_path": {
                        "type": "string",
                        "description": "目录路径"
                    },
                    "pattern": {
                        "type": "string",
                        "description": "文件匹配模式（如 *.py, *.jpg）",
                        "default": "*"
                    }
                },
                "required": ["dir_path"]
            },
            handler=list_directory
        ),
        Tool(
            name="extract_pdf_images",
            description=EXTRACT_PDF_IMAGES_DESCRIPTION,
            category=ToolCategory.FILE_SYSTEM,
            permission=PermissionLevel.AUTO,
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "PDF 文件的完整路径"
                    },
                    "page_numbers": {
                        "type": "string",
                        "description": "指定页码（如 '1,3,5-7'），留空提取所有页面",
                        "default": ""
                    }
                },
                "required": ["file_path"]
            },
            handler=extract_pdf_images
        ),
        Tool(
            name="render_pdf_page",
            description=RENDER_PDF_PAGE_DESCRIPTION,
            category=ToolCategory.FILE_SYSTEM,
            permission=PermissionLevel.AUTO,
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "PDF 文件的完整路径"
                    },
                    "page_number": {
                        "type": "integer",
                        "description": "页码（从1开始）",
                        "default": 1
                    },
                    "dpi": {
                        "type": "integer",
                        "description": "渲染分辨率（默认150）",
                        "default": 150
                    }
                },
                "required": ["file_path"]
            },
            handler=render_pdf_page
        ),
        Tool(
            name="get_pdf_info",
            description=GET_PDF_INFO_DESCRIPTION,
            category=ToolCategory.FILE_SYSTEM,
            permission=PermissionLevel.AUTO,
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "PDF 文件的完整路径"
                    }
                },
                "required": ["file_path"]
            },
            handler=get_pdf_info_tool
        )
        # 注意：read_image 已合并到 read_file（自动识别图片）
    ]

    return tools


def register_filesystem_tools(
    allowed_paths: list[str] = None,
    max_file_size_mb: float = 10.0
) -> None:
    """注册文件系统工具到全局注册表"""
    registry = get_registry()
    tools = create_filesystem_tools(allowed_paths, max_file_size_mb)
    for tool in tools:
        registry.register(tool)
