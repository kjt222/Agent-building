"""Tests for Agent architecture (Tool Use)."""

import asyncio
import json
import unittest
from unittest.mock import MagicMock, AsyncMock

from agent.tools.base import Tool, ToolResult, ToolCategory, PermissionLevel
from agent.tools.registry import ToolRegistry, get_registry
from agent.tools.executor import ToolExecutor, ExecutionContext
from agent.tools.knowledge import create_kb_tools
from agent.core.executor import AgentExecutor, AgentConfig, AgentState


class TestToolBase(unittest.TestCase):
    """Test Tool and ToolResult classes."""

    def test_tool_result_success(self):
        result = ToolResult(success=True, data={"key": "value"})
        self.assertTrue(result.success)
        self.assertEqual(result.data, {"key": "value"})
        self.assertIsNone(result.error)

    def test_tool_result_error(self):
        result = ToolResult(success=False, error="Something went wrong")
        self.assertFalse(result.success)
        self.assertEqual(result.error, "Something went wrong")

    def test_tool_result_to_dict(self):
        result = ToolResult(success=True, data="test", metadata={"time": 100})
        d = result.to_dict()
        self.assertEqual(d["success"], True)
        self.assertEqual(d["data"], "test")
        self.assertEqual(d["metadata"]["time"], 100)

    def test_tool_to_schema(self):
        def dummy_handler():
            return ToolResult(success=True)

        tool = Tool(
            name="test_tool",
            description="A test tool",
            category=ToolCategory.KNOWLEDGE,
            permission=PermissionLevel.AUTO,
            parameters={"type": "object", "properties": {}},
            handler=dummy_handler
        )
        schema = tool.to_schema()
        self.assertEqual(schema["type"], "function")
        self.assertEqual(schema["function"]["name"], "test_tool")
        self.assertEqual(schema["function"]["description"], "A test tool")


class TestToolRegistry(unittest.TestCase):
    """Test ToolRegistry."""

    def setUp(self):
        self.registry = ToolRegistry()
        self.registry.clear()

    def tearDown(self):
        self.registry.clear()

    def test_register_and_get(self):
        tool = Tool(
            name="my_tool",
            description="Test",
            category=ToolCategory.SYSTEM,
            permission=PermissionLevel.AUTO,
            parameters={},
            handler=lambda: ToolResult(success=True)
        )
        self.registry.register(tool)
        retrieved = self.registry.get("my_tool")
        self.assertEqual(retrieved.name, "my_tool")

    def test_list_all(self):
        tool1 = Tool(
            name="tool1", description="T1", category=ToolCategory.KNOWLEDGE,
            permission=PermissionLevel.AUTO, parameters={},
            handler=lambda: ToolResult(success=True)
        )
        tool2 = Tool(
            name="tool2", description="T2", category=ToolCategory.FILE_SYSTEM,
            permission=PermissionLevel.CONFIRM, parameters={},
            handler=lambda: ToolResult(success=True)
        )
        self.registry.register(tool1)
        self.registry.register(tool2)

        all_tools = self.registry.list_all()
        self.assertEqual(len(all_tools), 2)

        kb_tools = self.registry.list_all(category=ToolCategory.KNOWLEDGE)
        self.assertEqual(len(kb_tools), 1)
        self.assertEqual(kb_tools[0].name, "tool1")

    def test_to_schemas(self):
        tool = Tool(
            name="schema_test", description="For schema",
            category=ToolCategory.WEB, permission=PermissionLevel.AUTO,
            parameters={"type": "object", "properties": {"q": {"type": "string"}}},
            handler=lambda: ToolResult(success=True)
        )
        self.registry.register(tool)
        schemas = self.registry.to_schemas()
        self.assertEqual(len(schemas), 1)
        self.assertEqual(schemas[0]["function"]["name"], "schema_test")


class TestToolExecutor(unittest.TestCase):
    """Test ToolExecutor."""

    def setUp(self):
        self.registry = get_registry()
        self.registry.clear()
        self.executor = ToolExecutor(self.registry)

    def tearDown(self):
        self.registry.clear()

    def test_execute_success(self):
        def add_handler(a: int, b: int) -> ToolResult:
            return ToolResult(success=True, data=a + b)

        tool = Tool(
            name="add", description="Add two numbers",
            category=ToolCategory.CODE, permission=PermissionLevel.AUTO,
            parameters={}, handler=add_handler
        )
        self.registry.register(tool)

        result = asyncio.run(self.executor.execute("add", {"a": 2, "b": 3}))
        self.assertTrue(result.success)
        self.assertEqual(result.data, 5)

    def test_execute_tool_not_found(self):
        result = asyncio.run(self.executor.execute("nonexistent", {}))
        self.assertFalse(result.success)
        self.assertIn("not found", result.error)

    def test_execute_disabled_tool(self):
        tool = Tool(
            name="disabled_tool", description="Disabled",
            category=ToolCategory.SYSTEM, permission=PermissionLevel.AUTO,
            parameters={}, handler=lambda: ToolResult(success=True),
            enabled=False
        )
        self.registry.register(tool)

        result = asyncio.run(self.executor.execute("disabled_tool", {}))
        self.assertFalse(result.success)
        self.assertIn("disabled", result.error)


class TestKBTools(unittest.TestCase):
    """Test Knowledge Base tools."""

    def setUp(self):
        self.mock_config = {
            "knowledge_bases": [
                {"name": "kb1", "path": "/path/to/kb1"},
                {"name": "kb2", "path": "/path/to/kb2"},
            ],
            "active_kbs": ["kb1"]
        }

    def test_create_kb_tools(self):
        def config_loader():
            return self.mock_config

        tools = create_kb_tools(config_loader, None)
        # 3个工具：list_knowledge_bases（合并了get_kb_info）、search_knowledge_base、list_kb_files
        self.assertEqual(len(tools), 3)
        names = [t.name for t in tools]
        self.assertIn("list_knowledge_bases", names)  # 包含原 get_kb_info 功能
        self.assertIn("search_knowledge_base", names)
        self.assertIn("list_kb_files", names)

    def test_list_knowledge_bases(self):
        def config_loader():
            return self.mock_config

        tools = create_kb_tools(config_loader, None)
        list_kb = next(t for t in tools if t.name == "list_knowledge_bases")

        result = list_kb.handler()
        self.assertTrue(result.success)
        self.assertEqual(result.data["total_count"], 2)
        self.assertEqual(result.data["active_count"], 1)

        # Check kb1 is active, kb2 is not
        kbs = result.data["knowledge_bases"]
        kb1 = next(kb for kb in kbs if kb["name"] == "kb1")
        kb2 = next(kb for kb in kbs if kb["name"] == "kb2")
        self.assertTrue(kb1["active"])
        self.assertFalse(kb2["active"])

    def test_search_knowledge_base_no_rag_service(self):
        def config_loader():
            return self.mock_config

        tools = create_kb_tools(config_loader, None)  # No RAG service
        search_kb = next(t for t in tools if t.name == "search_knowledge_base")

        result = search_kb.handler(query="test")
        self.assertFalse(result.success)
        # 错误消息已更新为中文
        self.assertIn("RAG", result.error)  # "检索服务未配置。请检查 RAG 服务是否正确..."

    def test_search_knowledge_base_no_active_kbs(self):
        def config_loader():
            return {"knowledge_bases": [], "active_kbs": []}

        tools = create_kb_tools(config_loader, lambda x: MagicMock())
        search_kb = next(t for t in tools if t.name == "search_knowledge_base")

        result = search_kb.handler(query="test")
        self.assertFalse(result.success)
        # 错误消息已更新为中文
        self.assertIn("知识库", result.error)  # "没有激活的知识库..."


class MockModelAdapter:
    """Mock model adapter for testing AgentExecutor."""

    def __init__(self, responses):
        """
        Args:
            responses: List of response dicts to return in sequence.
                Each response can have: content, reasoning, tool_calls
        """
        self.responses = responses
        self.call_count = 0
        self.model = "mock-model"
        self.provider = "mock"

    def chat_stream_with_tools(self, messages, tools, **kwargs):
        if self.call_count >= len(self.responses):
            yield {"type": "content", "text": "No more responses"}
            return

        response = self.responses[self.call_count]
        self.call_count += 1

        if response.get("reasoning"):
            yield {"type": "reasoning", "text": response["reasoning"]}

        if response.get("content"):
            yield {"type": "content", "text": response["content"]}

        if response.get("tool_calls"):
            yield {"type": "tool_calls", "data": response["tool_calls"]}


class TestAgentExecutor(unittest.TestCase):
    """Test AgentExecutor."""

    def setUp(self):
        self.registry = get_registry()
        self.registry.clear()

        # Register a simple test tool
        def echo_handler(message: str) -> ToolResult:
            return ToolResult(success=True, data=f"Echo: {message}")

        tool = Tool(
            name="echo", description="Echo a message",
            category=ToolCategory.SYSTEM, permission=PermissionLevel.AUTO,
            parameters={"type": "object", "properties": {"message": {"type": "string"}}},
            handler=echo_handler
        )
        self.registry.register(tool)

    def tearDown(self):
        self.registry.clear()

    def test_simple_response_no_tools(self):
        """Test agent returns direct response without tool calls."""
        mock_model = MockModelAdapter([
            {"content": "Hello! How can I help you?"}
        ])

        agent = AgentExecutor(mock_model, AgentConfig(max_iterations=3))

        events = []
        async def collect_events():
            async for event in agent.run_stream("Hi"):
                events.append(event)

        asyncio.run(collect_events())

        # Should have content and done events
        content_events = [e for e in events if e.get("type") == "content"]
        done_events = [e for e in events if e.get("type") == "done"]

        self.assertTrue(len(content_events) > 0)
        self.assertEqual(len(done_events), 1)

    def test_tool_call_and_response(self):
        """Test agent calls tool and then responds."""
        mock_model = MockModelAdapter([
            # First response: call echo tool
            {
                "content": "Let me echo that for you.",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "echo",
                        "arguments": json.dumps({"message": "test"})
                    }
                }]
            },
            # Second response: final answer after tool result
            {"content": "The echo returned: Echo: test"}
        ])

        agent = AgentExecutor(mock_model, AgentConfig(max_iterations=5))

        events = []
        async def collect_events():
            async for event in agent.run_stream("Echo test"):
                events.append(event)

        asyncio.run(collect_events())

        # Should have tool_call, tool_result, content events
        tool_call_events = [e for e in events if e.get("type") == "tool_call"]
        tool_result_events = [e for e in events if e.get("type") == "tool_result"]

        self.assertEqual(len(tool_call_events), 1)
        self.assertEqual(len(tool_result_events), 1)

        # Verify tool was actually called
        tool_result = tool_result_events[0]["data"]["result"]
        self.assertTrue(tool_result["success"])
        self.assertEqual(tool_result["data"], "Echo: test")

    def test_max_iterations_limit(self):
        """Test agent stops after max iterations."""
        # Model always returns tool calls, never final response
        mock_model = MockModelAdapter([
            {"tool_calls": [{"id": "c1", "type": "function", "function": {"name": "echo", "arguments": '{"message":"1"}'}}]},
            {"tool_calls": [{"id": "c2", "type": "function", "function": {"name": "echo", "arguments": '{"message":"2"}'}}]},
            {"tool_calls": [{"id": "c3", "type": "function", "function": {"name": "echo", "arguments": '{"message":"3"}'}}]},
        ])

        agent = AgentExecutor(mock_model, AgentConfig(max_iterations=2))

        events = []
        async def collect_events():
            async for event in agent.run_stream("Loop"):
                events.append(event)

        asyncio.run(collect_events())

        # Should have error event about max iterations
        error_events = [e for e in events if e.get("type") == "error"]
        self.assertEqual(len(error_events), 1)
        self.assertIn("maximum iterations", error_events[0].get("message", ""))


if __name__ == "__main__":
    unittest.main()
