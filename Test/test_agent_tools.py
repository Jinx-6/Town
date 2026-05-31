"""
Tool 系统测试 — Tool / ToolResult / ToolRegistry / PromptAssembler tools section
全部 FakeLLM，不调真实模型。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.tool import (
    Tool, ToolResult, create_builtin_tools,
)
from agents.tool_registry import ToolRegistry


# ── FakeLLM with tools ───────────────────────────────
class FakeLLMWithTools:
    """模拟 LLMClient.generate_with_tools() 返回。"""

    def __init__(self, canned_tool_calls=None, canned_text="fake reply"):
        self.canned_tool_calls = canned_tool_calls or []
        self.canned_text = canned_text
        self.calls = []

    async def generate_with_tools(self, system_prompt: str, messages: list,
                                   tools: list) -> dict:
        self.calls.append({
            "system_prompt": system_prompt,
            "messages": messages,
            "tools": tools,
        })
        return {
            "text": self.canned_text,
            "tool_calls": self.canned_tool_calls,
            "finish_reason": "tool_calls" if self.canned_tool_calls else "stop",
        }


# ── Fixtures ─────────────────────────────────────────
@pytest.fixture
def registry():
    """含 3 个内置工具的空注册表。"""
    r = ToolRegistry()
    return r


@pytest.fixture
def registry_with_tools(registry):
    """已注册 3 个内置工具的注册表。"""
    # 工具不需要真实 retrieve_hub —— 用 None 测试注册/调度逻辑
    # search_own_memory 需要 retrieve_hub，这里用 FakeRetrieveHub
    from Memory.schema.retrieval import RetrievalResponse
    import time
    from Memory.schema.memory_item import MemoryItem, MemoryRole, MemoryStage, MemoryMetadata
    from datetime import datetime, timezone

    class FakeRetrieveHub:
        def retrieve(self, req, agent_id):
            results = []
            for i in range(2):
                item = MemoryItem(
                    id=f"fake_{i}",
                    content=f"test memory {i}",
                    role=MemoryRole.USER,
                    agent_id=agent_id,
                    timestamp=datetime.now(timezone.utc),
                    stage=MemoryStage.SEMANTIC,
                    metadata=MemoryMetadata(memory_type="event", is_factual_memory=True),
                )
                from Memory.schema.retrieval import RetrievedMemory
                from Memory.schema.routing import BackendTarget
                results.append(RetrievedMemory(item=item, score=0.9, source=BackendTarget.VECTOR))
            return RetrievalResponse(original_query=req.query, agent_id=agent_id, results=results)

    tools = create_builtin_tools(
        retrieve_hub=FakeRetrieveHub(),
        agent_id="test_agent",
        agent_name="Test",
        agent_role="tester",
        get_state_fn=lambda: "idle",
    )
    registry.register_all(tools)
    return registry


# ── ToolResult ────────────────────────────────────────
class TestToolResult:
    def test_defaults(self):
        r = ToolResult()
        assert r.name == ""
        assert r.success is False
        assert r.data is None
        assert r.error == ""

    def test_success(self):
        r = ToolResult(name="test", success=True, data={"k": "v"})
        assert r.success
        assert r.data == {"k": "v"}
        assert r.error == ""

    def test_failure(self):
        r = ToolResult(name="test", success=False, error="something wrong")
        assert not r.success
        assert r.error == "something wrong"


# ── Tool ─────────────────────────────────────────────
class TestTool:
    def test_fields(self):
        def noop():
            return ToolResult(name="t", success=True)

        t = Tool(
            name="my_tool", description="desc",
            parameters={"type": "object", "properties": {}, "required": []},
            execute=noop,
        )
        assert t.name == "my_tool"
        assert t.enabled is True
        assert t.return_direct is False
        result = t.execute()
        assert result.success


    def test_to_openai_schema(self):
        def noop():
            return ToolResult(name="t", success=True)

        t = Tool(name="search", description="desc",
                 parameters={"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
                 execute=noop)
        s = t.to_openai_schema()
        assert s["type"] == "function"
        assert s["function"]["name"] == "search"

    def test_disabled_tool(self):
        def noop():
            return ToolResult(name="t", success=True)

        t = Tool(name="off", description="", parameters={}, execute=noop, enabled=False)
        assert t.enabled is False


# ── ToolRegistry ─────────────────────────────────────
class TestToolRegistry:
    def test_register_and_get(self, registry):
        def noop():
            return ToolResult(name="a", success=True)
        t = Tool(name="a", description="", parameters={}, execute=noop)
        registry.register(t)
        assert registry.get("a") is t
        assert registry.get("x") is None

    def test_register_all(self, registry):
        def noop():
            return ToolResult(name="x", success=True)
        tools = [
            Tool(name="a", description="", parameters={}, execute=noop),
            Tool(name="b", description="", parameters={}, execute=noop),
        ]
        registry.register_all(tools)
        assert len(registry) == 2
        assert set(registry.names) == {"a", "b"}

    def test_list_enabled_excludes_disabled(self, registry):
        def noop():
            return ToolResult(name="x", success=True)
        registry.register(Tool(name="on",  description="", parameters={}, execute=noop, enabled=True))
        registry.register(Tool(name="off", description="", parameters={}, execute=noop, enabled=False))
        enabled = registry.list_enabled()
        assert len(enabled) == 1
        assert enabled[0].name == "on"

    def test_dispatch_success(self, registry_with_tools):
        result = registry_with_tools.dispatch("get_current_time")
        assert result.success
        assert "utc" in result.data

    def test_dispatch_not_registered(self, registry):
        result = registry.dispatch("nonexistent")
        assert not result.success
        assert "未注册" in result.error

    def test_dispatch_disabled(self, registry):
        def noop():
            return ToolResult(name="off", success=True)
        registry.register(Tool(name="off", description="", parameters={},
                               execute=noop, enabled=False))
        result = registry.dispatch("off")
        assert not result.success
        assert "已禁用" in result.error

    def test_dispatch_search_own_memory(self, registry_with_tools):
        result = registry_with_tools.dispatch(
            "search_own_memory", query="test", limit=3, memory_type="event"
        )
        assert result.success
        assert result.name == "search_own_memory"
        assert result.data["count"] == 2
        assert len(result.data["results"]) == 2
        for item in result.data["results"]:
            assert "content" in item
            assert "score" in item
            assert "memory_type" in item

    def test_dispatch_get_agent_state(self, registry_with_tools):
        result = registry_with_tools.dispatch("get_agent_state")
        assert result.success
        assert result.data["agent_id"] == "test_agent"
        assert result.data["agent_name"] == "Test"
        assert result.data["state"] == "idle"

    def test_to_openai_schemas(self, registry_with_tools):
        schemas = registry_with_tools.to_openai_schemas()
        assert len(schemas) == 3
        names = [s["function"]["name"] for s in schemas]
        assert "search_own_memory" in names
        assert "get_current_time" in names
        assert "get_agent_state" in names

    def test_contains(self, registry_with_tools):
        assert "search_own_memory" in registry_with_tools
        assert "nonexistent" not in registry_with_tools


# ── PromptAssembler tool_results section ──────────────
class TestPromptAssemblerToolSection:
    def test_tool_results_in_system_prompt(self):
        from Memory.processor.assembler import PromptAssembler
        from Memory.schema.retrieval import RetrievalResponse

        assembler = PromptAssembler(agent_name="Test")

        # 空记忆 + 一个成功的 tool_result
        tool_results = [{
            "name": "get_current_time",
            "success": True,
            "data": {"utc": "2026-05-29T10:00:00+00:00", "timestamp": 1716976800},
        }]
        messages = assembler.assemble(
            "现在几点了？",
            RetrievalResponse(original_query="现在几点了？", agent_id="test"),
            tool_results=tool_results,
        )
        system_content = messages[0]["content"]
        assert '<tool_result name="get_current_time">' in system_content
        assert '2026-05-29' in system_content
        assert '<user_current_input>' in messages[1]["content"]

    def test_tool_result_search_memory_format(self):
        from Memory.processor.assembler import PromptAssembler
        from Memory.schema.retrieval import RetrievalResponse

        assembler = PromptAssembler(agent_name="Test")
        tool_results = [{
            "name": "search_own_memory",
            "success": True,
            "data": {
                "count": 2,
                "results": [
                    {"content": "修了饮水机", "score": 0.95, "memory_type": "event"},
                    {"content": "买了咖啡",   "score": 0.80, "memory_type": "event"},
                ]
            },
        }]
        messages = assembler.assemble(
            "查询", RetrievalResponse(original_query="查询", agent_id="test"),
            tool_results=tool_results,
        )
        system = messages[0]["content"]
        assert "找到 2 条记忆" in system
        assert "修了饮水机" in system
        assert "买了咖啡" in system

    def test_tool_result_error_format(self):
        from Memory.processor.assembler import PromptAssembler
        from Memory.schema.retrieval import RetrievalResponse

        assembler = PromptAssembler(agent_name="Test")
        tool_results = [{
            "name": "search_own_memory",
            "success": False,
            "error": "连接超时",
        }]
        messages = assembler.assemble(
            "查询", RetrievalResponse(original_query="查询", agent_id="test"),
            tool_results=tool_results,
        )
        system = messages[0]["content"]
        assert "[search_own_memory] error: 连接超时" in system

    def test_no_tool_results_produces_clean_prompt(self):
        """无 tool_results 时 prompt 不含 tool 相关标签。"""
        from Memory.processor.assembler import PromptAssembler
        from Memory.schema.retrieval import RetrievalResponse

        assembler = PromptAssembler(agent_name="Test")
        messages = assembler.assemble(
            "你好",
            RetrievalResponse(original_query="你好", agent_id="test"),
            tool_results=None,
        )
        system = messages[0]["content"]
        assert "<tool_result" not in system  # no XML tag; guide text mention is fine


# ── generate_with_tools ──────────────────────────────
class TestLLMClientWithTools:
    def test_no_tool_calls_returns_text(self):
        llm = FakeLLMWithTools(canned_text="hello world", canned_tool_calls=[])
        result = llm.calls  # fake, just test structure
        # 模拟 async 调用
        import asyncio
        resp = asyncio.run(llm.generate_with_tools("sys", [], []))
        assert resp["text"] == "hello world"
        assert resp["tool_calls"] == []
        assert resp["finish_reason"] == "stop"

    def test_tool_calls_returned(self):
        llm = FakeLLMWithTools(
            canned_text=None,
            canned_tool_calls=[
                {"id": "call_1", "name": "get_current_time", "arguments": {}},
            ],
        )
        import asyncio
        resp = asyncio.run(llm.generate_with_tools("sys", [], []))
        assert resp["text"] is None
        assert len(resp["tool_calls"]) == 1
        assert resp["tool_calls"][0]["name"] == "get_current_time"
        assert resp["finish_reason"] == "tool_calls"

    def test_tools_passed_to_call(self):
        llm = FakeLLMWithTools()
        import asyncio
        asyncio.run(llm.generate_with_tools("sys", [], [
            {"type": "function", "function": {"name": "get_time", "description": "", "parameters": {}}}
        ]))
        assert len(llm.calls) == 1
        assert len(llm.calls[0]["tools"]) == 1
        assert llm.calls[0]["tools"][0]["function"]["name"] == "get_time"


# ── Tool → ToolRegistry → PromptAssembler 集成 ───────
class TestToolIntegration:
    def test_end_to_end_tool_to_prompt(self, registry_with_tools):
        """调度 tool → 取得 ToolResult → 注入 PromptAssembler 全链路。"""
        # Step 1: 调度工具
        result = registry_with_tools.dispatch("get_current_time")
        assert result.success

        # Step 2: 转为 assembler 可接受的格式
        tool_results = [{"name": result.name, "success": result.success, "data": result.data}]

        # Step 3: 注入 assembler
        from Memory.processor.assembler import PromptAssembler
        from Memory.schema.retrieval import RetrievalResponse
        assembler = PromptAssembler(agent_name="Test")
        messages = assembler.assemble(
            "现在几点？",
            RetrievalResponse(original_query="now", agent_id="test"),
            tool_results=tool_results,
        )
        system = messages[0]["content"]
        assert 'get_current_time' in system
        assert 'UTC' in system
        assert '<tool_result' in system

        # Step 4: 确认 tool 结果在主 prompt 的 system 部分（不在 user message）
        user = messages[1]["content"]
        assert "tool_result" not in user
        assert "<user_current_input>" in user
