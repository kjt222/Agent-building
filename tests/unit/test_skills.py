from __future__ import annotations

from pathlib import Path

import pytest

from agent.core.skills import (
    Skill,
    build_history_text,
    load_skills,
    parse_skill_file,
    select_skill,
)


PROJECT_SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills"


def _write_skill(tmp_path: Path, name: str, body: str) -> Path:
    skill_dir = tmp_path / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = skill_dir / "SKILL.md"
    path.write_text(body, encoding="utf-8")
    return path


def test_parse_skill_file_basic_frontmatter(tmp_path):
    body = (
        "---\n"
        "name: demo\n"
        "description: A demo skill.\n"
        "scope: demo_scope\n"
        "priority: 50\n"
        "triggers:\n"
        "  - \"(?i)foo\\\\bbar\"\n"
        "  - \"中文触发\"\n"
        "tools_base:\n"
        "  - Read\n"
        "  - Glob\n"
        "tools:\n"
        "  - WordRead\n"
        "---\n"
        "Body text only loaded when active.\n"
    )
    path = _write_skill(tmp_path, "demo", body)
    skill = parse_skill_file(path)
    assert skill is not None
    assert skill.name == "demo"
    assert skill.scope == "demo_scope"
    assert skill.priority == 50
    assert len(skill.triggers) == 2
    assert skill.tools_base == ("Read", "Glob")
    assert skill.tools == ("WordRead",)
    assert skill.all_tools() == ("Read", "Glob", "WordRead")
    assert "Body text" in skill.prompt_body


def test_parse_skill_file_rejects_missing_frontmatter(tmp_path):
    path = _write_skill(tmp_path, "broken", "no frontmatter here\n")
    assert parse_skill_file(path) is None


def test_load_skills_sorts_by_priority_desc(tmp_path):
    _write_skill(
        tmp_path,
        "low",
        "---\nname: low\npriority: 10\ntriggers:\n  - x\n---\nbody\n",
    )
    _write_skill(
        tmp_path,
        "high",
        "---\nname: high\npriority: 100\ntriggers:\n  - y\n---\nbody\n",
    )
    skills = load_skills(tmp_path)
    assert [s.name for s in skills] == ["high", "low"]


def test_select_skill_picks_highest_priority_match(tmp_path):
    _write_skill(
        tmp_path,
        "general",
        "---\nname: general\npriority: 10\ntriggers:\n  - \"image\"\n---\nbody\n",
    )
    _write_skill(
        tmp_path,
        "specific",
        "---\nname: specific\npriority: 100\ntriggers:\n  - \"image generation\"\n---\nbody\n",
    )
    skills = load_skills(tmp_path)
    chosen = select_skill("please run image generation now", skills=skills)
    assert chosen is not None
    assert chosen.name == "specific"

    fallback = select_skill("just an image", skills=skills)
    assert fallback is not None
    assert fallback.name == "general"

    none_match = select_skill("totally unrelated", skills=skills)
    assert none_match is None


def test_select_skill_history_triggers_required(tmp_path):
    _write_skill(
        tmp_path,
        "image-followup",
        (
            "---\n"
            "name: image-followup\n"
            "priority: 80\n"
            "triggers:\n"
            "  - color\n"
            "history_triggers:\n"
            "  - generated_image\n"
            "---\n"
            "body\n"
        ),
    )
    skills = load_skills(tmp_path)

    no_history = select_skill("change color to blue", skills=skills, history_text="")
    assert no_history is None

    with_history = select_skill(
        "change color to blue",
        skills=skills,
        history_text="prior turn produced generated_image artifact",
    )
    assert with_history is not None
    assert with_history.name == "image-followup"


def test_build_history_text_handles_messages():
    history = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "second"},
        "junk-not-a-dict",
        {"role": "tool", "content": None},
    ]
    text = build_history_text(history)
    assert "first" in text
    assert "second" in text
    assert "junk" not in text


def test_project_skills_dir_loads_and_contains_expected_scopes():
    skills = load_skills(PROJECT_SKILLS_DIR)
    scopes = {skill.scope for skill in skills}
    expected = {
        "office_word",
        "office_excel",
        "image_generation",
        "knowledge",
        "research",
        "artifact",
    }
    assert expected.issubset(scopes)


@pytest.mark.parametrize(
    "message, expected_scope",
    [
        ("修改 thesis.docx 的标题", "office_word"),
        ("write Word document formatting", "office_word"),
        ("修改一下这份表格", "office_excel"),
        ("update the spreadsheet sheet 'Report'", "office_excel"),
        ("生成一张数字人形象", "image_generation"),
        ("只根据知识库解释光刻", "knowledge"),
        ("查一下 OpenAI API 最新模型", "research"),
        ("写一个贪吃蛇 HTML 游戏", "artifact"),
    ],
)
def test_project_skills_route_real_messages(message, expected_scope):
    skills = load_skills(PROJECT_SKILLS_DIR)
    chosen = select_skill(message, skills=skills)
    if expected_scope is None:
        # The case explicitly expects no skill to match; if one does we
        # want a loud failure showing which one over-triggered.
        assert chosen is None, (
            f"expected no skill to match {message!r}, but "
            f"{(chosen.name if chosen else None)!r} did"
        )
        return
    assert chosen is not None, f"no skill matched: {message!r}"
    assert chosen.scope == expected_scope, (
        f"expected scope {expected_scope!r}, got {chosen.scope!r} "
        f"from skill {chosen.name!r}"
    )
