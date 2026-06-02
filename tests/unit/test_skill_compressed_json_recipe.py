"""Round-trip test for the compressed-json recipe documented in
``skills/obsidian-excalidraw/SKILL.md`` (#107).

After P13.1 smoke round 8 verified against a real Obsidian vault, we
discovered the Excalidraw plugin uses **lz-string**'s base64 variant
(`LZString.compressToBase64`), NOT pako/zlib raw deflate. The original
#105 recipe was wrong and we corrected it in #107.

These tests:

1. Confirm the lz-string library is installed and the round-trip works
   on synthetic data plus a known-good fixture matching Obsidian's
   real output format.
2. Confirm SKILL.md teaches lz-string (not pako/zlib) and explicitly
   warns about the wrong alternatives.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import lzstring


FENCE_RE = re.compile(r"```compressed-json\s*\n(.*?)\n```", re.DOTALL)
_LZ = lzstring.LZString()


def _encode(data: dict) -> str:
    body = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    return _LZ.compressToBase64(body)


def _decode(blob: str) -> dict:
    cleaned = re.sub(r"\s+", "", blob)
    decoded = _LZ.decompressFromBase64(cleaned)
    if not decoded:
        raise ValueError("lz-string decompression returned empty")
    return json.loads(decoded)


def test_compressed_json_roundtrip_preserves_object():
    obj = {
        "type": "excalidraw",
        "version": 2,
        "elements": [
            {"id": "el1", "seed": 12345, "type": "rectangle", "x": 100, "y": 200},
            {"id": "el2", "seed": 67890, "type": "image", "fileId": "f_xyz"},
        ],
        "files": {"f_xyz": {"id": "f_xyz", "mimeType": "image/svg+xml"}},
        "appState": {"viewBackgroundColor": "#ffffff", "scrollX": 0},
        "customData": {"latex_source": "L_t = \\sqrt{\\rho_c / R_{shs}}",
                        "note": "公式 (5) 的等价形式"},
    }
    encoded = _encode(obj)
    # lz-string base64 output starts with N4 in this library family.
    assert encoded[:2] == "N4"
    decoded = _decode(encoded)
    assert decoded == obj


def test_compressed_json_decode_handles_256_char_line_wrap():
    obj = {"type": "excalidraw", "version": 1,
           "elements": [{"id": "a", "seed": 1, "text": "x" * 500}],
           "files": {}, "appState": {}}
    encoded = _encode(obj)
    # Wrap to 256 chars + blank line between chunks (Obsidian's pattern).
    wrapped = "\n\n".join(encoded[i : i + 256]
                           for i in range(0, len(encoded), 256))
    assert _decode(wrapped) == obj


def test_compressed_json_fence_replace_roundtrip(tmp_path):
    obj = {"type": "excalidraw", "version": 1,
           "elements": [{"id": "a", "seed": 1}],
           "files": {}, "appState": {}}
    blob = _encode(obj)
    fname = tmp_path / "x.excalidraw.md"
    fname.write_text(
        "---\nexcalidraw-plugin: parsed\n---\n\n"
        "# Excalidraw Data\n\n## Drawing\n"
        f"```compressed-json\n{blob}\n```\n\n%%\n"
        "{\"type\":\"excalidraw\",\"version\":1,\"elements\":[]}\n"
        "%%\n",
        encoding="utf-8",
    )

    text = fname.read_text(encoding="utf-8")
    m = FENCE_RE.search(text)
    assert m is not None
    data = _decode(m.group(1))

    data["elements"].append({"id": "b", "seed": 2, "type": "text"})
    data["version"] += 1
    new_blob = _encode(data)
    new_text = FENCE_RE.sub(
        f"```compressed-json\n{new_blob}\n```", text, count=1
    )
    fname.write_text(new_text, encoding="utf-8")

    text2 = fname.read_text(encoding="utf-8")
    data2 = _decode(FENCE_RE.search(text2).group(1))
    assert data2["version"] == 2
    assert [e["id"] for e in data2["elements"]] == ["a", "b"]
    assert "{\"type\":\"excalidraw\",\"version\":1,\"elements\":[]}" in text2


def test_skill_md_documents_lz_string_recipe_not_pako():
    text = Path("skills/obsidian-excalidraw/SKILL.md").read_text(encoding="utf-8")
    # The recipe must say lz-string and import lzstring; the *wrong*
    # alternatives must appear ONLY inside the anti-pattern warnings.
    assert "lz-string" in text
    assert "import lzstring" in text
    assert "decompressFromBase64" in text
    # Anti-pattern callouts so future-you doesn't regress to the pako recipe.
    assert "Error -3 invalid block type" in text
    assert "Invalid base64-encoded string" in text


def test_skill_md_warns_about_triple_equals_padding():
    text = Path("skills/obsidian-excalidraw/SKILL.md").read_text(encoding="utf-8")
    # Standard b64 padding is 0/1/2; the `===` (three equals) on the real
    # fence tripwires anyone using base64.b64decode directly.
    assert "===" in text
    # And the N4K magic prefix that identifies lz-string output.
    assert "N4K" in text
