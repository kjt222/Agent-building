"""L3 vision judge (P14.3.1).

Send a rendered image + task description to a multimodal model and ask:
did the agent's output match the user's goal? Returns structured
JudgeReport that the runner folds into AcceptanceVerdict.

Backend resolution (first available wins):
  1. ANTHROPIC_API_KEY → anthropic SDK, default `claude-sonnet-4-6`
  2. OPENAI_API_KEY    → openai SDK,    default `gpt-5.5-vision`
  3. Doubao key in     → openai-compat ↦ Volcano Ark vision model
     keyring under
     `doubao-code.llm.openai_compat`

Soft-fail by design — missing keys / missing image / LLM error / malformed
JSON all return `verdict=unknown` with the reason in `error`. Smoke runs
never hard-fail because L3 couldn't run.
"""

from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

JudgeVerdict = Literal["pass", "partial", "fail", "unknown"]
JudgeConfidence = Literal["high", "med", "low", "unknown"]


@dataclass
class JudgeReport:
    verdict: JudgeVerdict = "unknown"
    confidence: JudgeConfidence = "unknown"
    findings: list[str] = field(default_factory=list)
    unmet_requirements: list[str] = field(default_factory=list)
    raw_response: str = ""
    error: str | None = None
    model: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "confidence": self.confidence,
            "findings": list(self.findings),
            "unmet_requirements": list(self.unmet_requirements),
            "model": self.model,
            "error": self.error,
            # raw_response can be large; keep but truncated
            "raw_response": self.raw_response[:2000],
        }


_PROMPT_TEMPLATE = """\
你是一个任务结果视觉**布局**验收官。请仅基于提供的截图判断 agent 是否完成了
用户提出的任务的**布局部分**。

# 用户原始任务
{user_prompt}

# 期望结果
{expected_outcome}

# 关于这张截图（非常重要，先读完再判断）
这是 agent 完成任务后由 Python 布局近似渲染器生成的图，**不是**目标应用
（Obsidian / Word / 浏览器等）的真实截图。

约定如下，请严格按此理解每个元素：

1. **黄色背景方框 + 里面写着 LaTeX 源码**（如 `R = r_c × ...`）= **一个公式已正确插入**。
   katex 真正的图形渲染发生在 Obsidian 里，不在这张图里。**有黄底框 + 合法 LaTeX
   源码 = 公式 OK**，不要因为"看不到 katex 图形"就判 fail。
2. **红色方框（带标签）** = frame 容器（类似 PPT 的占位框）。
3. **黑色边框矩形 / 椭圆 / 线 / 箭头** = 几何元素，照原样判断。
4. **黑色文字** = 文字元素，照原样判断。

# 请评估这些方面（不评估 katex 像素质量）
- 元素**有没有正确出现**（数量、类型符合预期）
- 元素**位置**是否合理（靠近原文 anchor、不越界、不和别的元素严重堆叠）
- 元素**分组**是否符合预期（同组的内容是不是被同一个 frame 框住、能否一起移动）
- 视觉**信息密度**是否合理（不是单纯一堆空框）

# 回答格式（仅输出一个 JSON 对象，不要其他文字、不要 markdown 围栏）
{{
  "verdict": "pass" | "partial" | "fail",
  "confidence": "high" | "med" | "low",
  "findings": ["简短观察 1", "简短观察 2", ...],
  "unmet_requirements": ["用户明确要求但没满足的点 1", ...]
}}

判定标准：
- **pass**: 公式 / 内容都到位（黄底框含合法 LaTeX 即视为公式到位），分组合理，
  无明显错位 / 堆叠 / 越界。
- **partial**: 大部分到位，但有明显但非关键的布局缺陷（少量错位 / 没完全分组 /
  字号偏小）。
- **fail**: 核心要求没满足 —— 比如**根本没有相关公式或内容**（连黄底框 + LaTeX
  都没有），或者所有内容严重堆叠 / 越界 / 找不到任务相关产出。

再次强调：黄底框 + LaTeX 源码 = 公式已就位。**不要因为"是占位"就判 fail**。
"""


def _encode_image(path: Path) -> tuple[str, str]:
    data = path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    media_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return media_type, b64


def _parse_response(text: str) -> tuple[JudgeVerdict, JudgeConfidence, list[str], list[str]]:
    # Try to extract a JSON object (model may wrap in ```json fences or add chatter)
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return "unknown", "unknown", [], []
    blob = m.group(0)
    try:
        obj = json.loads(blob)
    except Exception:
        return "unknown", "unknown", [], []
    v = obj.get("verdict")
    if v not in ("pass", "partial", "fail"):
        v = "unknown"
    c = obj.get("confidence")
    if c not in ("high", "med", "low"):
        c = "unknown"
    findings = obj.get("findings") or []
    unmet = obj.get("unmet_requirements") or []
    findings = [str(x) for x in findings if isinstance(x, (str, int, float))]
    unmet = [str(x) for x in unmet if isinstance(x, (str, int, float))]
    return v, c, findings, unmet


def _resolve_backend(prefer: str | None) -> tuple[str, str, dict] | None:
    """Pick the first available backend. Returns (backend, model, kwargs) or None.

    Order: caller's `prefer` first if its key is present, else anthropic →
    openai → doubao. Each tuple's `kwargs` carries everything the backend
    function needs (api_key + base_url).
    """
    candidates: list[tuple[str, str, dict]] = []
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        candidates.append(("anthropic", "claude-sonnet-4-6",
                           {"api_key": anthropic_key}))
    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        candidates.append(("openai", "gpt-5.5",
                           {"api_key": openai_key,
                            "base_url": "https://api.openai.com/v1"}))
    # Doubao vision via Ark gateway (openai-compatible).
    try:
        import keyring  # type: ignore
        from agent.credentials import SERVICE_NAME
        doubao_key = keyring.get_password(SERVICE_NAME, "doubao-code.llm.openai_compat")
    except Exception:
        doubao_key = None
    if doubao_key:
        candidates.append(("doubao", "doubao-1-5-vision-pro-32k-250115",
                           {"api_key": doubao_key,
                            "base_url": "https://ark.cn-beijing.volces.com/api/v3"}))

    # Try OpenAI key from keyring as fallback (project's research / 11 profile).
    if not openai_key:
        try:
            import keyring  # type: ignore
            from agent.credentials import SERVICE_NAME
            for ref in ("11.llm.openai", "gpt-5.4.llm.openai", "research.llm.openai"):
                kk = keyring.get_password(SERVICE_NAME, ref)
                if kk:
                    candidates.append(("openai", "gpt-5.5",
                                       {"api_key": kk,
                                        "base_url": "https://api.openai.com/v1"}))
                    break
        except Exception:
            pass

    if not candidates:
        return None

    if prefer:
        for c in candidates:
            if c[0] == prefer:
                return c
    return candidates[0]


def _call_anthropic(model: str, kwargs: dict, media_type: str, b64: str,
                    prompt: str, timeout: float) -> str:
    import anthropic  # type: ignore
    client = anthropic.Anthropic(api_key=kwargs["api_key"], timeout=timeout)
    resp = client.messages.create(
        model=model, max_tokens=1024,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": media_type, "data": b64}},
                {"type": "text", "text": prompt},
            ],
        }],
    )
    parts = [getattr(b, "text", "") for b in (resp.content or [])
             if getattr(b, "type", None) == "text"]
    return "\n".join(parts).strip()


def _call_openai_compatible(model: str, kwargs: dict, media_type: str, b64: str,
                            prompt: str, timeout: float) -> str:
    """Works for both OpenAI proper and openai-compatible vision endpoints
    (gpt-5.5-vision, Doubao Ark vision)."""
    from openai import OpenAI  # type: ignore
    client = OpenAI(api_key=kwargs["api_key"], base_url=kwargs.get("base_url"),
                    timeout=timeout)
    resp = client.chat.completions.create(
        model=model, max_tokens=1024,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:{media_type};base64,{b64}"}},
                {"type": "text", "text": prompt},
            ],
        }],
    )
    return (resp.choices[0].message.content or "").strip()


def judge(
    rendered_image: Path,
    task_spec: dict[str, Any],
    *,
    backend: str | None = None,
    model: str | None = None,
    timeout: float = 60.0,
) -> JudgeReport:
    """Render-then-judge entrypoint. `backend` ∈ {anthropic, openai, doubao, None}."""
    report = JudgeReport(model=model or "")

    if not rendered_image.exists():
        report.error = f"image not found: {rendered_image}"
        return report

    chosen = _resolve_backend(prefer=backend)
    if chosen is None:
        report.error = (
            "no vision backend key available "
            "(ANTHROPIC_API_KEY / OPENAI_API_KEY / doubao keyring all unset); "
            "skipping L3 vision judge"
        )
        return report

    backend_name, default_model, kwargs = chosen
    chosen_model = model or default_model
    report.model = f"{backend_name}:{chosen_model}"

    media_type, b64 = _encode_image(rendered_image)
    prompt = _PROMPT_TEMPLATE.format(
        user_prompt=task_spec.get("user_prompt", "(unspecified)"),
        expected_outcome=task_spec.get("expected_outcome", "(unspecified)"),
    )

    try:
        if backend_name == "anthropic":
            text = _call_anthropic(chosen_model, kwargs, media_type, b64, prompt, timeout)
        else:  # openai or doubao both OpenAI-compatible
            text = _call_openai_compatible(chosen_model, kwargs, media_type, b64,
                                           prompt, timeout)
    except Exception as exc:
        report.error = f"vision call failed ({backend_name}): {exc}"
        return report

    report.raw_response = text
    v, c, findings, unmet = _parse_response(text)
    report.verdict = v
    report.confidence = c
    report.findings = findings
    report.unmet_requirements = unmet
    return report
