"""
MemoryAwareAgent 测试 — FakeLLM 不调真实模型。
覆盖: 事实存储/闲聊短路/问答过滤/写前检索隔离/响应结构
"""
import sys
import os
import time
import asyncio
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from Test.mocks import MockLLMClient
from Memory.schema.memory_item import MemoryRole
from Memory.schema.routing import BackendTarget
from Memory.schema.retrieval import RetrievalRequest, RetrievedMemory

from Memory.storage.working_cache import WorkingMemoryCache
from Memory.storage.sqlite_log import SQLiteLogStorage

from Memory.processor.router import WriteRouter
from Memory.processor.planner import QueryPlanner
from Memory.processor.assembler import PromptAssembler
from Memory.processor.intent_classifier import create_intent_classifier

from Memory.policies.scoring import MemoryScorer
from Memory.policies.retrieval_policy import RetrievalPolicy
from Memory.policies.update_policy import UpdatePolicy

from Memory.hub.async_dispatcher import AsyncDispatcher
from Memory.hub.ingest import IngestHub
from Memory.hub.retrieve import RetrieveHub

from agents.memory_aware_agent import (
    MemoryAwareAgent, AgentResponse, AgentState, AgentRunResult,
)


# ── Fake LLM ──────────────────────────────────────────
class FakeLLM:
    """记录调用参数并返回预设回复，异步但立即返回。"""

    def __init__(self, canned_response: str = "(FakeLLM 回复)"):
        self.canned = canned_response
        self.calls: list = []

    async def generate(self, system_prompt: str, messages: list) -> str:
        self.calls.append((system_prompt, messages))
        return self.canned


# ── Mock VectorStore ──────────────────────────────────
class MockVectorStore:
    def __init__(self):
        self.items: dict = {}

    def add(self, item):
        self.items[item.id] = item

    def search(self, query: str, agent_id: str, limit: int = 5,
               metadata_filters=None) -> list:
        results = []
        for item in self.items.values():
            if item.agent_id != agent_id:
                continue
            if metadata_filters:
                meta = item.metadata if isinstance(item.metadata, dict) else item.metadata.model_dump()
                skip = any(meta.get(k) != v for k, v in metadata_filters.items())
                if skip:
                    continue
            results.append(RetrievedMemory(item=item, score=0.25, source=BackendTarget.VECTOR))
        return results[:limit]

    def delete_by_agent(self, agent_id: str):
        self.items = {k: v for k, v in self.items.items() if v.agent_id != agent_id}


# ── Helper ────────────────────────────────────────────
def _respond(agent, user_input: str) -> AgentResponse:
    """同步包装，避免 pytest-asyncio 依赖。"""
    return asyncio.run(agent.respond(user_input))


def _run(agent, *, turns=1, user_inputs=None):
    """同步包装 run()。"""
    return asyncio.run(agent.run(turns=turns, user_inputs=user_inputs))


# ── Failing LLM ──────────────────────────────────────
class FailingLLM:
    """模拟 LLM 异常。"""

    def __init__(self):
        self.calls: list = []

    async def generate(self, system_prompt: str, messages: list) -> str:
        self.calls.append((system_prompt, messages))
        raise RuntimeError("FakeLLM 模拟异常")


# ── Tool-aware FakeLLM ───────────────────────────────
class ToolAwareLLM:
    """支持 tool loop 的 FakeLLM：第一轮返回 tool_calls，第二轮返回纯文本。"""

    def __init__(self, tool_calls: list = None, final_text: str = "final reply"):
        self._tool_calls = tool_calls or []
        self._final_text = final_text
        self.calls_generate = []       # generate() 调用记录
        self.calls_generate_with_tools = []  # generate_with_tools() 调用记录
        self._tool_round = 0

    async def generate(self, system_prompt: str, messages: list) -> str:
        self.calls_generate.append((system_prompt, messages))
        return self._final_text

    async def generate_with_tools(self, system_prompt: str, messages: list,
                                   tools: list) -> dict:
        self.calls_generate_with_tools.append({
            "system_prompt": system_prompt, "messages": messages, "tools": tools,
        })
        if self._tool_round < len(self._tool_calls):
            tc = self._tool_calls[self._tool_round]
            self._tool_round += 1
            return {
                "text": None,
                "tool_calls": [tc],
                "finish_reason": "tool_calls",
            }
        return {"text": self._final_text, "tool_calls": [], "finish_reason": "stop"}


# ── Fixture ───────────────────────────────────────────
@pytest.fixture
def agent():
    agent_id = "test_agent"
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test.db")

    cache = WorkingMemoryCache()
    sqlite = SQLiteLogStorage(db_path=db_path)
    vector_store = MockVectorStore()
    router = WriteRouter()

    backends = {BackendTarget.SQLITE: sqlite, BackendTarget.VECTOR: vector_store}
    dispatcher = AsyncDispatcher(
        backends=backends, extractor=None, conflict_resolver=None,
        index_manager=None, update_policy=UpdatePolicy(),
    )
    ingest = IngestHub(working_cache=cache, router=router, dispatcher=dispatcher)

    planner = QueryPlanner(llm=MockLLMClient())
    scorer = MemoryScorer()
    policy = RetrievalPolicy()
    retrieve = RetrieveHub(
        planner=planner, cache=cache, sqlite=sqlite,
        vector_store=vector_store, graph_store=None,
        scorer=scorer, policy=policy,
    )

    classifier = create_intent_classifier(use_llm=False)
    assembler = PromptAssembler(agent_name="测试助手")
    fake_llm = FakeLLM(canned_response="收到，我已记住。")

    from demo_cli import apply_time_boost

    a = MemoryAwareAgent(
        agent_id=agent_id, agent_name="测试助手", agent_role="测试工程师",
        ingest_hub=ingest, retrieve_hub=retrieve, intent_classifier=classifier,
        prompt_assembler=assembler, llm_client=fake_llm,
        time_boost_fn=apply_time_boost,
    )
    a._sqlite = sqlite
    a._vector = vector_store
    a._cache = cache
    a._fake_llm = fake_llm
    a._tmpdir = tmpdir
    return a


# ── Tests ─────────────────────────────────────────────
class TestMemoryAwareAgent:

    def test_fact_statement_stored_and_retrieved(self, agent):
        resp1 = _respond(agent, "我昨天帮你修了发电机")
        assert resp1.intent_type == "fact_statement"
        assert "收到" in resp1.text
        assert len(agent._fake_llm.calls) == 1
        time.sleep(0.3)

        items = agent._sqlite.search_text(query="发电机", agent_id="test_agent", limit=5)
        assert len(items) >= 1
        assert items[0].metadata.memory_type == "event"
        assert items[0].metadata.is_factual_memory is True

        resp2 = _respond(agent, "我昨天帮你做了什么")
        assert resp2.intent_type == "qa_query"
        assert len(resp2.rewritten_queries) >= 1

    def test_chitchat_skips_retrieval_not_persisted(self, agent):
        resp = _respond(agent, "哈哈")
        assert resp.intent_type == "chitchat"
        assert resp.retrieved_memories == []
        time.sleep(0.3)

        items = agent._sqlite.search_text(query="哈哈", agent_id="test_agent", limit=5)
        assert len(items) == 0
        assert len(agent._cache) >= 1

    def test_qa_query_uses_metadata_filters(self, agent):
        _respond(agent, "我前天修了饮水机")
        time.sleep(0.3)

        agent.ingest.process_message(
            content="我之前问过什么问题", role=MemoryRole.USER,
            agent_id="test_agent", intent_type="qa_query",
        )
        time.sleep(0.3)

        req = RetrievalRequest(
            query="我之前做过什么", limit=5, agent_id="test_agent",
            metadata_filters={"is_factual_memory": True},
        )
        results = agent.retrieve.retrieve(req, agent_id="test_agent")
        for r in results.results:
            mt = r.item.metadata.memory_type
            assert mt != "query", f"不应该包含 query 类型记忆，但出现了 {mt}"

    def test_retrieve_before_write_no_self_pollution(self, agent):
        _respond(agent, "我喜欢喝咖啡")
        time.sleep(0.3)

        resp = _respond(agent, "我喜欢喝什么")
        contents = [r.item.content for r in resp.retrieved_memories]
        assert "我喜欢喝什么" not in contents
        assert any("咖啡" in c for c in contents), f"应检索到之前的事实，实际: {contents}"

    def test_agent_response_structure(self, agent):
        resp = _respond(agent, "我昨天修了电脑")
        assert isinstance(resp, AgentResponse)
        assert len(resp.text) > 0
        assert resp.intent_type == "fact_statement"
        assert isinstance(resp.retrieved_memories, list)
        assert isinstance(resp.pre_boost_snapshot, list)
        assert isinstance(resp.context_messages, list)
        assert len(resp.context_messages) == 2
        assert resp.context_messages[0]["role"] == "system"
        assert resp.context_messages[1]["role"] == "user"
        assert "<user_current_input>" in resp.context_messages[1]["content"]

    def test_prompt_contains_required_xml_tags(self, agent):
        _respond(agent, "我昨天帮你修了发电机")
        time.sleep(0.3)
        _respond(agent, "我昨天做了什么")

        call = agent._fake_llm.calls[-1]
        system_content = call[0]
        user_content = call[1][0]["content"] if call[1] else ""

        assert "retrieved_factual_memories" in system_content or \
               "retrieved_factual_memories" in user_content
        assert "user_current_input" in user_content

    def test_multiple_turns_accumulate_memories(self, agent):
        """多轮对话，记忆持续累积。"""
        _respond(agent, "我叫张三")
        time.sleep(0.2)
        _respond(agent, "我是Python工程师")
        time.sleep(0.2)

        all_items = agent._sqlite.search_text(query="张三", agent_id="test_agent", limit=10)
        assert len(all_items) >= 1

        all_items2 = agent._sqlite.search_text(query="Python", agent_id="test_agent", limit=10)
        assert len(all_items2) >= 1


# ── Phase 2: run() 状态循环测试 ───────────────────────
class TestPhase2AgentLoop:

    def test_run_with_user_inputs(self, agent):
        """多个外部输入产生多个 AgentResponse。"""
        result = _run(agent, turns=3, user_inputs=[
            "我叫张三", "我喜欢Python", "我昨天修了电脑"
        ])
        assert len(result.turns) == 3
        assert result.final_state == AgentState.IDLE
        for resp in result.turns:
            assert isinstance(resp, AgentResponse)
            assert not resp.is_autonomous
            assert len(resp.text) > 0
            assert resp.intent_type in (
                "fact_statement", "qa_query", "chitchat", "task_instruction"
            )

    def test_run_autonomous_turns(self, agent):
        """自主轮使用 deterministic stimulus，每轮都有响应。"""
        agent.reset_stimulus()
        result = _run(agent, turns=3, user_inputs=[None, None, None])
        assert len(result.turns) == 3
        for resp in result.turns:
            assert resp.is_autonomous
            assert len(resp.text) > 0

    def test_agent_state_transitions(self, agent):
        """状态顺序: IDLE → PERCEIVING → THINKING → ACTING → IDLE。"""
        states_seen = []

        def tracker(old, new, turn):
            states_seen.append((turn, old.value, new.value))

        result = _run(agent, turns=2, user_inputs=["你好", "谢谢"])
        result2 = asyncio.run(agent.run(
            turns=2, user_inputs=["你好", "谢谢"],
            on_state_change=tracker,
        ))
        # 每轮 4 次变迁 + 最后 IDLE
        assert len(states_seen) >= 4
        # 验证存在典型变迁
        transitions = [(o, n) for _, o, n in states_seen]
        assert ("idle", "perceiving") in transitions
        assert ("perceiving", "thinking") in transitions or ("idle", "thinking") in transitions
        assert ("acting", "idle") in transitions or ("thinking", "acting") in transitions

    def test_run_handles_llm_error(self, agent):
        """LLM 异常时 AgentRunResult.errors 非空，不死锁。"""
        agent.llm = FailingLLM()
        result = _run(agent, turns=2, user_inputs=["你好", "谢谢"])
        assert len(result.errors) >= 1
        assert result.final_state == AgentState.IDLE
        # 每轮都尝试了但都失败了
        assert len(result.turns) == 2
        for resp in result.turns:
            assert resp.error != ""

    def test_autonomous_turn_does_not_pollute_factual_memory(self, agent):
        """自主轮不进入 factual memory。"""
        agent.reset_stimulus()
        time.sleep(0.1)

        # 先存一条真实事实
        _respond(agent, "我昨天修了饮水机")
        time.sleep(0.4)

        # 运行自主轮
        _run(agent, turns=2, user_inputs=[None, None])
        time.sleep(0.4)

        # 搜索所有记忆：自主轮内容应标记为 self_check
        all_items = agent._sqlite.search_text(query="回顾", agent_id="test_agent", limit=10)
        for item in all_items:
            mt = getattr(item.metadata, "memory_type", "unknown")
            is_fact = getattr(item.metadata, "is_factual_memory", True)
            if "self_check" in str(mt) or not is_fact:
                continue  # 自主轮内容正确标记
            assert item.metadata.memory_type != "self_check" or not item.metadata.is_factual_memory, (
                f"自主轮内容不应进入 factual memory: {item.content[:50]}"
            )

        # 验证 is_factual_memory=True 的过滤不返回自主轮内容
        from Memory.schema.retrieval import RetrievalRequest
        req = RetrievalRequest(
            query="回顾 对话", limit=10, agent_id="test_agent",
            metadata_filters={"is_factual_memory": True},
        )
        results = agent.retrieve.retrieve(req, agent_id="test_agent")
        for r in results.results:
            mt = r.item.metadata.memory_type if hasattr(r.item.metadata, 'memory_type') else "?"
            assert mt != "self_check", (
                f"is_factual_memory 过滤应排除 self_check 类型，但: {r.item.content[:50]}"
            )


# ── Phase 3: Tool Loop 测试 ──────────────────────────
class TestPhase3ToolLoop:

    @pytest.fixture
    def agent_with_tools(self, agent):
        """带 3 个内置工具的 agent。"""
        from agents.tool import create_builtin_tools
        from agents.tool_registry import ToolRegistry

        # 给 retrieve_hub 创建一个 FakeRetrieveHub
        from Memory.schema.retrieval import RetrievalResponse
        from Memory.schema.memory_item import MemoryItem, MemoryRole, MemoryStage, MemoryMetadata
        from datetime import datetime, timezone

        class FakeRH:
            def retrieve(self, req, agent_id):
                results = []
                for i in range(2):
                    item = MemoryItem(
                        id=f"fake_{i}", content=f"test memory {i}",
                        role=MemoryRole.USER, agent_id=agent_id,
                        timestamp=datetime.now(timezone.utc),
                        stage=MemoryStage.SEMANTIC,
                        metadata=MemoryMetadata(memory_type="event", is_factual_memory=True),
                    )
                    from Memory.schema.retrieval import RetrievedMemory
                    from Memory.schema.routing import BackendTarget
                    results.append(RetrievedMemory(item=item, score=0.9, source=BackendTarget.VECTOR))
                return RetrievalResponse(original_query=req.query, agent_id=agent_id, results=results)

        registry = ToolRegistry()
        tools = create_builtin_tools(
            retrieve_hub=FakeRH(),
            agent_id="test_agent",
            agent_name="Test",
            agent_role="tester",
            get_state_fn=lambda: "idle",
        )
        registry.register_all(tools)
        agent.tool_registry = registry
        return agent

    def test_no_tool_loop_when_no_registry(self, agent):
        """未注入 tool_registry 时行为不变。"""
        agent.tool_registry = None
        resp = _respond(agent, "你好")
        assert resp.tool_results == []
        assert len(resp.text) > 0

    def test_no_tool_loop_when_no_enabled_tools(self, agent):
        """空 registry（无 enabled 工具）时行为不变。"""
        from agents.tool_registry import ToolRegistry
        agent.tool_registry = ToolRegistry()
        resp = _respond(agent, "你好")
        assert resp.tool_results == []
        assert len(resp.text) > 0

    def test_tool_loop_runs_when_tools_available(self, agent_with_tools):
        """有 enabled 工具时 tool loop 运行并收集结果。"""
        agent_with_tools.llm = ToolAwareLLM(
            tool_calls=[
                {"id": "c1", "name": "get_current_time", "arguments": {}},
            ],
            final_text="现在是下午3点。",
        )
        resp = _respond(agent_with_tools, "现在几点了？")
        # 验证 tool loop 运行了
        assert len(agent_with_tools.llm.calls_generate_with_tools) >= 1
        # 验证最终生成用的是纯 generate()，不是 generate_with_tools()
        assert len(agent_with_tools.llm.calls_generate) >= 1
        # tool_results 在 AgentResponse 中
        assert len(resp.tool_results) >= 1
        assert resp.tool_results[0]["name"] == "get_current_time"
        assert resp.tool_results[0]["success"] is True

    def test_tool_result_not_ingested_as_factual_memory(self, agent_with_tools):
        """ToolResult 不进入 factual memory 路径。"""
        agent_with_tools.llm = ToolAwareLLM(
            tool_calls=[
                {"id": "c1", "name": "search_own_memory",
                 "arguments": {"query": "test"}},
            ],
            final_text="我没有找到相关记忆。",
        )
        resp = _respond(agent_with_tools, "查一下记忆")
        time.sleep(0.4)

        # tool_results 在 AgentResponse 中
        assert len(resp.tool_results) >= 1
        assert resp.tool_results[0]["name"] == "search_own_memory"

        # tool result 的 data 内容（test memory 0/1）不应进入 SQLite 作为用户记忆
        all_items = agent_with_tools._sqlite.search_text(
            query="test memory", agent_id="test_agent", limit=10
        )
        # 工具结果不应出现在 SQLite 中
        for item in all_items:
            assert "test memory" not in item.content, (
                f"工具检索结果不应被当成用户输入存储: {item.content}"
            )

        # 用户的原始输入"查一下记忆"被正常存储了
        user_items = agent_with_tools._sqlite.search_text(
            query="查一下记忆", agent_id="test_agent", limit=10
        )
        assert len(user_items) >= 1

    def test_tool_result_in_prompt_not_user_input(self, agent_with_tools):
        """工具结果出现在 <tool_result> 中，不在 <user_current_input> 中。"""
        agent_with_tools.llm = ToolAwareLLM(
            tool_calls=[
                {"id": "c1", "name": "get_current_time", "arguments": {}},
            ],
            final_text="现在是UTC时间。",
        )
        resp = _respond(agent_with_tools, "几点了？")
        # 最终 prompt 的 user message 不含 tool_result
        for msg in resp.context_messages:
            if msg["role"] == "user":
                assert "tool_result" not in msg["content"]
                assert "<user_current_input>" in msg["content"]
            if msg["role"] == "system":
                assert "<tool_result" in msg["content"]

    def test_multiple_tool_calls_in_loop(self, agent_with_tools):
        """LLM 连续调用两个工具后回复文本。"""
        agent_with_tools.llm = ToolAwareLLM(
            tool_calls=[
                {"id": "c1", "name": "get_current_time", "arguments": {}},
                {"id": "c2", "name": "get_agent_state", "arguments": {}},
            ],
            final_text="时间和状态都已查询完毕。",
        )
        resp = _respond(agent_with_tools, "查一下时间和状态")
        assert len(resp.tool_results) == 2
        assert resp.tool_results[0]["name"] == "get_current_time"
        assert resp.tool_results[1]["name"] == "get_agent_state"
        # tool loop 调用了 3 次（2 轮 tool + 1 轮 stop）
        assert len(agent_with_tools.llm.calls_generate_with_tools) == 3
        # 最终纯文本调用了一次 generate
        assert len(agent_with_tools.llm.calls_generate) == 1
