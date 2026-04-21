"""Pytest Configuration - 测试配置

目录结构：
    tests/
    ├── unit/           # 纯逻辑测试，mock 所有外部依赖
    ├── integration/    # 需要真实 server / API key / DB
    ├── agent_eval/     # Agent 评估框架（runner, grader, reporter）
    ├── perf/           # 性能基准测试
    ├── graders/        # 评分工具（供 agent_eval 和 unit 共用）
    ├── fixtures/       # 测试数据
    │   └── agent_tasks/  # YAML 任务定义
    └── results/        # 测试运行产出（git-ignored）

标记：
- @pytest.mark.model_grader: 需要 LLM API 的测试（默认跳过）
- @pytest.mark.slow: 慢速测试（默认跳过）
- @pytest.mark.integration: 集成测试（默认跳过）

使用方式：
    # 仅运行 unit 测试（快速，CI 默认）
    pytest tests/unit/

    # 运行全部（含 integration）
    pytest tests/ --run-integration

    # 运行包含 model_grader 的测试
    pytest tests/ --run-model-grader

    # 运行 perf 基准
    pytest tests/perf/ --run-slow
"""

import pytest


def pytest_addoption(parser):
    """添加命令行选项"""
    parser.addoption(
        "--run-model-grader",
        action="store_true",
        default=False,
        help="Run tests that require LLM API calls (model_grader tests)",
    )
    parser.addoption(
        "--run-slow",
        action="store_true",
        default=False,
        help="Run slow tests",
    )
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run integration tests (require real server / API key)",
    )


def pytest_configure(config):
    """配置自定义标记"""
    config.addinivalue_line(
        "markers",
        "model_grader: mark test as requiring LLM API (skipped unless --run-model-grader)",
    )
    config.addinivalue_line(
        "markers",
        "slow: mark test as slow (skipped unless --run-slow)",
    )
    config.addinivalue_line(
        "markers",
        "integration: mark test as integration test (skipped unless --run-integration)",
    )


def pytest_collection_modifyitems(config, items):
    """根据命令行选项跳过特定测试"""

    if not config.getoption("--run-model-grader"):
        skip_model_grader = pytest.mark.skip(
            reason="Need --run-model-grader option to run (requires LLM API)"
        )
        for item in items:
            if "model_grader" in item.keywords:
                item.add_marker(skip_model_grader)

    if not config.getoption("--run-slow"):
        skip_slow = pytest.mark.skip(reason="Need --run-slow option to run")
        for item in items:
            if "slow" in item.keywords:
                item.add_marker(skip_slow)

    if not config.getoption("--run-integration"):
        skip_integration = pytest.mark.skip(
            reason="Need --run-integration option to run"
        )
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip_integration)


# ============================================================
# 通用 Fixtures
# ============================================================


@pytest.fixture
def mock_llm_response():
    """Mock LLM 响应的 fixture"""
    responses = []

    def _mock_response(content):
        responses.append(content)

    return _mock_response


@pytest.fixture
def sample_conversation():
    """示例对话数据"""
    return [
        {"role": "user", "content": "你好，我是张三"},
        {"role": "assistant", "content": "你好张三！有什么可以帮助你的吗？"},
        {"role": "user", "content": "我想了解一下光刻技术"},
        {"role": "assistant", "content": "光刻技术是半导体制造的核心工艺..."},
        {"role": "user", "content": "具体原理是什么？"},
        {"role": "assistant", "content": "光刻的基本原理是利用光化学反应..."},
    ]


@pytest.fixture
def sample_tool_calls():
    """示例工具调用数据"""
    return [
        {"name": "search_knowledge_base", "arguments": {"query": "光刻技术原理"}},
        {"name": "remember_fact", "arguments": {"fact": "用户名叫张三", "category": "fact"}},
        {"name": "get_system_config", "arguments": {"config_type": "llm"}},
    ]


@pytest.fixture
def temp_test_file(tmp_path):
    """创建临时测试文件"""

    def _create_file(name, content):
        file_path = tmp_path / name
        file_path.write_text(content, encoding="utf-8")
        return str(file_path)

    return _create_file
