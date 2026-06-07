"""
Tests for the Skill Layer.
"""
import sys
import os
import asyncio
import tempfile
import shutil
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from Test.mocks import MockLLMClient
from agents.event import Event
from skills.base import Skill, SkillResult
from skills.registry import SkillRegistry
from skills.builtin import ObservePublicEventSkill, SummarizeRecentConversationSkill

from Memory.storage.working_cache import WorkingMemoryCache
from Memory.storage.sqlite_log import SQLiteLogStorage
from Memory.processor.router import WriteRouter
from Memory.processor.planner import QueryPlanner
from Memory.processor.assembler import PromptAssembler
from Memory.processor.intent_classifier import create_intent_classifier
from Memory.policies.scoring import MemoryScorer
from Memory.policies.retrieval_policy import RetrievalPolicy
from Memory.policies.update_policy import UpdatePolicy
from Memory.hub.ingest import IngestHub
from Memory.hub.retrieve import RetrieveHub
from Memory.hub.async_dispatcher import AsyncDispatcher
from Memory.schema.routing import BackendTarget
from Memory.schema.retrieval import RetrievedMemory
from Memory.schema.memory_item import MemoryRole
from agents.memory_aware_agent import MemoryAwareAgent


def _a(fn, *args, **kwargs):
    return asyncio.run(fn(*args, **kwargs))


class _SumFakeLLM:
    async def generate(self, system_prompt, messages):
        return "这是一段测试摘要：对话涉及日常问候和工作讨论。"
    async def generate_with_tools(self, system_prompt, messages, tools):
        return {"text": "", "tool_calls": [], "finish_reason": "stop"}


class _ObsFakeLLM:
    async def generate(self, system_prompt, messages):
        return "观察记录完毕"
    async def generate_with_tools(self, system_prompt, messages, tools):
        return {"text": "", "tool_calls": [], "finish_reason": "stop"}


class _MockVectorStore:
    def __init__(self):
        self._items = {}

    def add(self, item):
        self._items[item.id] = item

    def search(self, query, limit=5, agent_id="", metadata_filters=None):
        results = []
        for item in self._items.values():
            if agent_id and item.agent_id != agent_id:
                continue
            results.append(RetrievedMemory(item=item, score=0.85,
                           source=BackendTarget.VECTOR))
        return sorted(results, key=lambda r: r.score, reverse=True)[:limit]

    def delete_by_agent(self, agent_id):
        self._items = {k: v for k, v in self._items.items()
                       if v.agent_id != agent_id}


def _build_agent(agent_id, agent_name, tmpdir, canned):
    db_path = os.path.join(tmpdir, f"skill_{agent_id}.db")
    cache = WorkingMemoryCache()
    sqlite = SQLiteLogStorage(db_path=db_path)
    vector_store = _MockVectorStore()

    backends = {BackendTarget.SQLITE: sqlite, BackendTarget.VECTOR: vector_store}
    dispatcher = AsyncDispatcher(
        backends=backends, extractor=None,
        conflict_resolver=None, index_manager=None,
        update_policy=UpdatePolicy(),
    )
    ingest = IngestHub(working_cache=cache, router=WriteRouter(),
                       dispatcher=dispatcher)

    planner = QueryPlanner(llm=MockLLMClient())
    scorer = MemoryScorer()
    retrieval_policy = RetrievalPolicy()
    retrieve = RetrieveHub(
        planner=planner, cache=cache, sqlite=sqlite,
        vector_store=vector_store, graph_store=None,
        scorer=scorer, policy=retrieval_policy,
    )

    classifier = create_intent_classifier(use_llm=False)
    assembler = PromptAssembler(agent_name=agent_name)

    class _FakeLLM:
        async def generate(self, system_prompt, messages):
            return canned.format(name=agent_name)
        async def generate_with_tools(self, system_prompt, messages, tools):
            return {"text": "", "tool_calls": [], "finish_reason": "stop"}

    return MemoryAwareAgent(
        agent_id=agent_id, agent_name=agent_name, agent_role="测试",
        ingest_hub=ingest, retrieve_hub=retrieve,
        intent_classifier=classifier, prompt_assembler=assembler,
        llm_client=_FakeLLM(),
        base_system_prompt="测试",
    )


# ── Test 1: SkillRegistry ──────────────────────────────

class TestSkillRegistry:

    def test_register_and_get(self):
        reg = SkillRegistry()
        skill = ObservePublicEventSkill()
        reg.register(skill)
        assert reg.get("observe_public_event") is skill
        assert "observe_public_event" in reg
        assert len(reg) == 1

    def test_list_enabled_excludes_disabled(self):
        reg = SkillRegistry()
        s1 = ObservePublicEventSkill()
        s2 = ObservePublicEventSkill()
        s2.name = "observe_disabled"
        s2.enabled = False

        reg.register(s1)
        reg.register(s2)

        assert len(reg.list_enabled()) == 1
        assert len(reg.list_all()) == 2

    def test_run_if_applicable_runs_matching_skills(self):
        tmpdir = tempfile.mkdtemp()
        try:
            agent = _build_agent("sk_a", "A", tmpdir, canned="ok")

            reg = SkillRegistry()
            skill = ObservePublicEventSkill()
            skill.name = "observe_public_event"  # use original name
            reg.register(skill)

            event = Event(
                source_agent_id="other", topic="town.public",
                content="今天天气真好", type="message",
                metadata={"agent_name": "路人甲"},
            )

            results = _a(reg.run_if_applicable, event, agent)
            assert len(results) == 1
            assert results[0].success
            assert "观察" in results[0].data.get("observation", "")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_run_if_applicable_skips_non_matching(self):
        tmpdir = tempfile.mkdtemp()
        try:
            agent = _build_agent("sk_b", "B", tmpdir, canned="ok")

            reg = SkillRegistry()
            reg.register(ObservePublicEventSkill())

            # system event + not public topic → should not match
            event = Event(
                source_agent_id="system", topic="private.dm",
                content="你好", type="system",
            )

            results = _a(reg.run_if_applicable, event, agent)
            assert len(results) == 0
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ── Test 2: ObservePublicEventSkill ────────────────────

class TestObservePublicEventSkill:

    def test_writes_observation_to_agent_memory(self):
        tmpdir = tempfile.mkdtemp()
        try:
            agent = _build_agent("obs_a", "张三", tmpdir, canned="ok")
            skill = ObservePublicEventSkill()

            event = Event(
                source_agent_id="li_si", topic="town.public",
                content="今天天气不错，适合散步", type="message",
                metadata={"agent_name": "李四"},
            )

            result = _a(skill.run, event, agent)
            assert result.success
            time.sleep(0.5)

            sqlite_results = agent.retrieve.sqlite.search_with_metadata(
                query="观察 李四", agent_id="obs_a", limit=10)
            contents = [r.content for r in sqlite_results]
            assert any("李四" in c and "散步" in c for c in contents), \
                f"应有观察记忆, got: {contents}"
            assert any("[观察]" in c for c in contents), \
                f"应有 observation 标记, got: {contents}"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_applies_to_returns_false_for_system_source(self):
        skill = ObservePublicEventSkill()
        event = Event(source_agent_id="system", topic="town.public",
                      content="sys msg", type="system")
        assert skill.applies_to(event) is False

    def test_applies_to_returns_false_for_private_topic(self):
        skill = ObservePublicEventSkill()
        event = Event(source_agent_id="bob", topic="private.dm",
                      content="secret", type="message")
        assert skill.applies_to(event) is False

    def test_applies_to_returns_true_for_public_agent_message(self):
        skill = ObservePublicEventSkill()
        event = Event(source_agent_id="bob", topic="town.public",
                      content="hello", type="message")
        assert skill.applies_to(event) is True


# ── Test 3: Observation memory isolation ───────────────

class TestObservationIsolation:

    def test_observation_does_not_leak_to_other_agent(self):
        tmpdir = tempfile.mkdtemp()
        try:
            agent_a = _build_agent("obs_aa", "A", tmpdir, canned="ok")
            agent_b = _build_agent("obs_bb", "B", tmpdir, canned="ok")

            skill = ObservePublicEventSkill()

            event = Event(
                source_agent_id="charlie", topic="town.public",
                content="我明天要去旅行", type="message",
                metadata={"agent_name": "Charlie"},
            )

            # Run skill only for agent A
            result = _a(skill.run, event, agent_a)
            assert result.success
            time.sleep(1.0)

            # Agent A should have the observation
            a_contents = [r.content for r in
                         agent_a.retrieve.sqlite.search_with_metadata(
                             query="观察 Charlie 旅行", agent_id="obs_aa", limit=10)]
            assert any("Charlie" in c for c in a_contents), \
                f"A 应有观察记忆, got: {a_contents}"

            # Agent B should NOT have the observation
            b_contents = [r.content for r in
                         agent_b.retrieve.sqlite.search_with_metadata(
                             query="观察 Charlie 旅行", agent_id="obs_bb", limit=10)]
            assert not any("Charlie" in c for c in b_contents), \
                f"B 不应有 A 的观察记忆, got: {b_contents}"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ── Test 4: SummarizeRecentConversationSkill ───────────

class TestSummarizeRecentConversationSkill:

    def test_summarize_produces_output(self):
        tmpdir = tempfile.mkdtemp()
        try:
            agent = _build_agent("sum_a", "张三", tmpdir, canned="ok")
            # Swap in a predictable LLM for summarization
            sum_llm = _SumFakeLLM()

            skill = SummarizeRecentConversationSkill(
                llm_client=sum_llm, max_events=5)

            events = [
                Event(source_agent_id="li_si", topic="town.public",
                      content="今天天气真好", type="message"),
                Event(source_agent_id="zhang_san", topic="town.public",
                      content="是啊，适合出去玩", type="message"),
                Event(source_agent_id="li_si", topic="town.public",
                      content="那我们下午一起去公园吧", type="message"),
            ]

            result = _a(skill.summarize, events, agent)
            assert result.success
            assert "测试摘要" in result.data.get("summary", "")
            assert result.data.get("event_count") == 3
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_summarize_writes_to_memory(self):
        tmpdir = tempfile.mkdtemp()
        try:
            agent = _build_agent("sum_b", "李四", tmpdir, canned="ok")
            sum_llm = _SumFakeLLM()

            skill = SummarizeRecentConversationSkill(
                llm_client=sum_llm, max_events=5)

            events = [
                Event(source_agent_id="wang_wu", topic="town.public",
                      content="项目 deadline 是周五", type="message"),
            ]

            result = _a(skill.summarize, events, agent)
            assert result.success
            time.sleep(0.5)

            sqlite_results = agent.retrieve.sqlite.search_with_metadata(
                query="对话摘要 deadline", agent_id="sum_b", limit=10)
            contents = [r.content for r in sqlite_results]
            assert any("对话摘要" in c for c in contents), \
                f"应有摘要记忆, got: {contents}"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_summarize_handles_empty_events(self):
        skill = SummarizeRecentConversationSkill(
            llm_client=_SumFakeLLM())

        tmpdir = tempfile.mkdtemp()
        try:
            agent = _build_agent("sum_c", "C", tmpdir, canned="ok")
            result = _a(skill.summarize, [], agent)
            assert not result.success
            assert "没有事件" in result.error
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
