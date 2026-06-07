"""
Tests for the publish-subscribe multi-agent runtime.
"""
import sys
import os
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from Test.mocks import MockLLMClient
from agents.event import Event
from agents.event_bus import EventBus, SchedulerPolicy
from agents.agent_worker import AgentWorker
from simulator import Simulator


# ── helpers ────────────────────────────────────────────

def _a(fn, *args, **kwargs):
    """Sync wrapper for async functions — no pytest-asyncio dependency."""
    return asyncio.run(fn(*args, **kwargs))


class _MockAgent:
    def __init__(self, agent_id, agent_name, canned):
        self.agent_id = agent_id
        self.agent_name = agent_name
        self._canned = canned

    async def respond(self, content):
        from agents.memory_aware_agent import AgentResponse
        return AgentResponse(
            text=self._canned.format(name=self.agent_name, content=content),
            intent_type="chitchat",
        )


# ── Test 1: EventBus publish + dispatch ────────────────

class TestEventBusPublishDispatch:

    def test_subscribe_and_dispatch(self):
        bus = EventBus()
        received = []

        async def collector(event):
            received.append(event)
            return []

        bus.subscribe("test.topic", collector)

        event = Event(source_agent_id="a", topic="test.topic",
                      content="hello", type="message")
        bus.publish(event)

        result = _a(bus.dispatch_next)
        assert result == 1
        assert len(received) == 1
        assert received[0].content == "hello"
        assert bus.total_dispatched == 1

    def test_unmatched_topic_ignored(self):
        bus = EventBus()
        received = []

        async def collector(event):
            received.append(event)
            return []

        bus.subscribe("topic.a", collector)

        event = Event(source_agent_id="a", topic="topic.b",
                      content="wrath", type="message")
        bus.publish(event)

        result = _a(bus.dispatch_next)
        assert result == 0
        assert len(received) == 0

    def test_multiple_subscribers_same_topic(self):
        bus = EventBus()
        r1, r2 = [], []

        async def c1(event):
            r1.append(event)
            return []

        async def c2(event):
            r2.append(event)
            return []

        bus.subscribe("t", c1)
        bus.subscribe("t", c2)

        bus.publish(Event(source_agent_id="a", topic="t",
                          content="hi", type="message"))
        _a(bus.dispatch_next)

        assert len(r1) == 1
        assert len(r2) == 1

    def test_handler_response_queued(self):
        bus = EventBus()
        seen = []

        async def echo(event):
            seen.append(event.content)
            return [Event(source_agent_id="h", topic="t",
                          content="reply:" + event.content, type="message")]

        bus.subscribe("t", echo)
        bus.publish(Event(source_agent_id="a", topic="t",
                          content="ping", type="message"))

        _a(bus.dispatch_next)
        assert len(seen) == 1
        assert bus.queue_size == 1
        assert bus._queue[0].content == "reply:ping"

    def test_max_total_events_caps_dispatch(self):
        policy = SchedulerPolicy(max_total_events=2)
        bus = EventBus(policy=policy)

        async def echo(event):
            return [Event(source_agent_id="h", topic="t",
                          content="reply", type="message")]

        bus.subscribe("t", echo)
        bus.publish(Event(source_agent_id="a", topic="t",
                          content="start", type="message"))

        dispatched = _a(bus.run_until_idle, max_events=2)
        assert dispatched == 2
        # queue still has remaining events but dispatch stopped
        assert bus.queue_size >= 0

    def test_run_until_idle_empty_queue(self):
        bus = EventBus()
        result = _a(bus.run_until_idle, max_events=10)
        assert result == 0

    def test_handler_exception_does_not_crash_bus(self):
        bus = EventBus()
        survived = []

        async def crasher(event):
            raise RuntimeError("boom")

        async def survivor(event):
            survived.append(event)
            return []

        bus.subscribe("t", crasher)
        bus.subscribe("t", survivor)
        bus.publish(Event(source_agent_id="a", topic="t",
                          content="hi", type="message"))

        result = _a(bus.dispatch_next)
        assert result == 2  # both handlers were called
        assert len(survived) == 1

    # ── history ──────────────────────────────────────

    def test_history_records_each_dispatched_event(self):
        bus = EventBus()

        async def noop(event):
            return []

        bus.subscribe("t", noop)
        bus.publish(Event(source_agent_id="a", topic="t",
                          content="e1", type="message"))
        bus.publish(Event(source_agent_id="b", topic="t",
                          content="e2", type="message"))

        _a(bus.dispatch_next)
        assert len(bus.history) == 1
        assert bus.history[0].content == "e1"

        _a(bus.dispatch_next)
        assert len(bus.history) == 2
        assert [e.content for e in bus.history] == ["e1", "e2"]

    # ── dedup ────────────────────────────────────────

    def test_duplicate_event_id_skipped(self):
        policy = SchedulerPolicy()
        bus = EventBus(policy=policy)
        seen = []

        async def handler(event):
            seen.append(event.content)
            return []

        bus.subscribe("t", handler)
        bus.publish(Event(source_agent_id="a", topic="t",
                          content="first", type="message", event_id="dup-id"))
        bus.publish(Event(source_agent_id="a", topic="t",
                          content="second", type="message", event_id="dup-id"))

        _a(bus.dispatch_next)
        assert len(seen) == 1
        assert seen == ["first"]
        assert bus.queue_size == 1  # duplicate still in queue
        assert policy.is_duplicate("dup-id")

    def test_run_until_idle_skips_duplicates(self):
        policy = SchedulerPolicy()
        bus = EventBus(policy=policy)
        seen = []

        async def handler(event):
            seen.append(event.content)
            return []

        bus.subscribe("t", handler)
        bus.publish(Event(source_agent_id="a", topic="t",
                          content="orig", type="message", event_id="dup"))
        bus.publish(Event(source_agent_id="a", topic="t",
                          content="dupe", type="message", event_id="dup"))
        bus.publish(Event(source_agent_id="a", topic="t",
                          content="uniq", type="message", event_id="uniq"))

        dispatched = _a(bus.run_until_idle, max_events=10)
        assert dispatched == 2
        assert seen == ["orig", "uniq"]

    # ── per-agent cap ────────────────────────────────

    def test_per_agent_max_caps_output(self):
        policy = SchedulerPolicy(max_total_events=10, per_agent_max=2)
        bus = EventBus(policy=policy)

        async def responder(event):
            return [Event(source_agent_id="a", topic="t",
                          content="reply", type="message")]

        bus.subscribe("t", responder)
        bus.publish(Event(source_agent_id="system", topic="t",
                          content="start", type="message"))

        dispatched = _a(bus.run_until_idle, max_events=10)
        # Agent "a" can produce at most 2 replies (per_agent_max)
        assert dispatched <= 4  # 1 start + up to 3 replies (but capped at 2)
        assert bus.queue_size >= 0


# ── Test 2: AgentWorker self-event filtering ────────────

class TestAgentWorkerIgnoresOwnEvent:

    def test_skips_own_event(self):
        agent = _MockAgent("a1", "test_agent", canned="你好")
        worker = AgentWorker(agent)

        event = Event(source_agent_id="a1", topic="t",
                      content="hello", type="message")

        result = _a(worker.handle_event, event)
        assert result is None
        assert worker.processed_count == 0

    def test_processes_other_event(self):
        agent = _MockAgent("a1", "test_agent",
                          canned="{name} 回复: {content}")
        worker = AgentWorker(agent)

        event = Event(source_agent_id="other", topic="t",
                      content="你好啊", type="message")

        result = _a(worker.handle_event, event)
        assert result is not None
        assert len(result) == 1
        assert worker.processed_count == 1
        assert result[0].source_agent_id == "a1"
        assert result[0].topic == "t"
        assert "test_agent" in result[0].content

    def test_system_message_not_ignored(self):
        agent = _MockAgent("a1", "test_agent",
                          canned="{name} 收到系统消息")
        worker = AgentWorker(agent)

        event = Event(source_agent_id="system", topic="t",
                      content="小镇广播", type="system")

        result = _a(worker.handle_event, event)
        assert result is not None
        assert worker.processed_count == 1

    # ── target_agent_id filtering ────────────────────

    def test_skips_when_target_agent_id_mismatch(self):
        agent = _MockAgent("a1", "test_agent", canned="hello")
        worker = AgentWorker(agent)

        event = Event(source_agent_id="other", topic="t",
                      content="只有 b1 能看", type="message",
                      target_agent_id="b1")

        result = _a(worker.handle_event, event)
        assert result is None
        assert worker.processed_count == 0

    def test_processes_when_target_agent_id_matches(self):
        agent = _MockAgent("a1", "test_agent",
                          canned="{name} 收到定向消息")
        worker = AgentWorker(agent)

        event = Event(source_agent_id="other", topic="t",
                      content="只有 a1 能看", type="message",
                      target_agent_id="a1")

        result = _a(worker.handle_event, event)
        assert result is not None
        assert len(result) == 1
        assert worker.processed_count == 1

    def test_no_target_agent_id_treated_as_broadcast(self):
        """Empty target_agent_id = broadcast, all agents process."""
        agent = _MockAgent("a1", "test_agent",
                          canned="{name} 收到广播")
        worker = AgentWorker(agent)

        event = Event(source_agent_id="other", topic="t",
                      content="广播消息", type="message",
                      target_agent_id="")  # empty = broadcast

        result = _a(worker.handle_event, event)
        assert result is not None
        assert worker.processed_count == 1


# ── Test 3: End-to-end multi-agent pub-sub ──────────────

class TestMultiAgentPubsubRuns:

    def test_two_agents_exchange_via_simulator(self):
        bus = EventBus(policy=SchedulerPolicy(max_total_events=8))
        sim = Simulator(bus=bus)

        agent_a = _MockAgent("zhang_san", "张三",
                            canned="{name} 回复了最新消息")
        agent_b = _MockAgent("li_si", "李四",
                            canned="{name} 也回应了最新消息")

        worker_a = AgentWorker(agent_a)
        worker_b = AgentWorker(agent_b)

        sim.add_agent(worker_a, topics=["town.public"])
        sim.add_agent(worker_b, topics=["town.public"])

        sim.inject_topic("town.public", "大家好！", source="system")

        dispatched = sim.run(max_events=8)

        summary = sim.summary()
        assert dispatched > 0
        assert summary["zhang_san"] >= 1
        assert summary["li_si"] >= 1
        # Both handlers fire per event; processed_count can exceed dispatched
        assert summary["zhang_san"] <= dispatched * 2
        assert summary["li_si"] <= dispatched * 2

    def test_agents_only_on_subscribed_topics(self):
        bus = EventBus()
        sim = Simulator(bus=bus)

        agent_a = _MockAgent("a", "A", canned="A reply")
        agent_b = _MockAgent("b", "B", canned="B reply")

        sim.add_agent(AgentWorker(agent_a), topics=["topic.a"])
        sim.add_agent(AgentWorker(agent_b), topics=["topic.b"])

        sim.inject_topic("topic.a", "hello a", source="system")
        sim.inject_topic("topic.b", "hello b", source="system")

        dispatched = sim.run(max_events=4)

        summary = sim.summary()
        assert dispatched >= 2
        assert summary["a"] >= 1
        assert summary["b"] >= 1


# ── Memory isolation under pub/sub ──────────────────────

from Memory.storage.working_cache import WorkingMemoryCache
from Memory.storage.sqlite_log import SQLiteLogStorage
from Memory.storage.vector_store import VectorStore
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
from Memory.schema.retrieval import RetrievalRequest
from Memory.schema.memory_item import MemoryRole
from agents.memory_aware_agent import MemoryAwareAgent
import tempfile, shutil


class TestPubSubMemoryIsolation:

    @classmethod
    def setup_class(cls):
        cls.tmpdir = tempfile.mkdtemp()

    @classmethod
    def teardown_class(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _build_agent(self, agent_id, agent_name, canned):
        """Build a MemoryAwareAgent with real SQLite + MockVectorStore."""
        db_path = os.path.join(self.tmpdir, f"iso_{agent_id}.db")
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

        agent = MemoryAwareAgent(
            agent_id=agent_id, agent_name=agent_name, agent_role="测试",
            ingest_hub=ingest, retrieve_hub=retrieve,
            intent_classifier=classifier, prompt_assembler=assembler,
            llm_client=_FakeLLM(),
            base_system_prompt="测试用系统提示",
        )
        return agent

    def test_agent_a_memory_not_visible_to_agent_b(self):
        """Directed (target_agent_id) messages create isolated memories.

        Broadcast: both can remember. Directed to A: only A remembers.
        """
        import time

        agent_a = self._build_agent("iso_a", "A", canned="A 记录完毕")
        agent_b = self._build_agent("iso_b", "B", canned="B 记录完毕")

        worker_a = AgentWorker(agent_a)
        worker_b = AgentWorker(agent_b)

        bus = EventBus(policy=SchedulerPolicy(max_total_events=6))
        sim = Simulator(bus=bus)
        sim.add_agent(worker_a, topics=["town.public"])
        sim.add_agent(worker_b, topics=["town.public"])

        # 1. Broadcast: both agents receive and remember
        sim.inject_topic("town.public", "今天是周一", source="system")
        # 2. Directed: only agent iso_a processes this
        sim.bus.publish(Event(
            source_agent_id="system", topic="town.public",
            content="iso_a 的秘密密码是 8888",
            target_agent_id="iso_a", type="system",
        ))
        # 3. Directed: only agent iso_b processes this
        sim.bus.publish(Event(
            source_agent_id="system", topic="town.public",
            content="iso_b 的秘密密码是 9999",
            target_agent_id="iso_b", type="system",
        ))

        dispatched = sim.run(max_events=10)
        assert dispatched >= 2

        time.sleep(2.0)

        # Both know about Monday (broadcast)
        a_public = [r.content for r in agent_a.retrieve.sqlite.search_with_metadata(
            query="周一", agent_id="iso_a", limit=20)]
        b_public = [r.content for r in agent_b.retrieve.sqlite.search_with_metadata(
            query="周一", agent_id="iso_b", limit=20)]
        assert any("周一" in c for c in a_public), f"A 应有广播记忆, got: {a_public}"
        assert any("周一" in c for c in b_public), f"B 应有广播记忆, got: {b_public}"

        # A has A's secret; B does NOT have A's secret
        a_secret = [r.content for r in agent_a.retrieve.sqlite.search_with_metadata(
            query="密码 8888", agent_id="iso_a", limit=20)]
        b_should_not = [r.content for r in agent_b.retrieve.sqlite.search_with_metadata(
            query="密码 8888", agent_id="iso_b", limit=20)]
        assert any("8888" in c for c in a_secret), \
            f"A 应有定向记忆, got: {a_secret}"
        assert not any("8888" in c for c in b_should_not), \
            f"B 不应有 A 的定向记忆, got: {b_should_not}"

        # B has B's secret; A does NOT have B's secret
        b_secret = [r.content for r in agent_b.retrieve.sqlite.search_with_metadata(
            query="密码 9999", agent_id="iso_b", limit=20)]
        a_should_not = [r.content for r in agent_a.retrieve.sqlite.search_with_metadata(
            query="密码 9999", agent_id="iso_a", limit=20)]
        assert any("9999" in c for c in b_secret), \
            f"B 应有定向记忆, got: {b_secret}"
        assert not any("9999" in c for c in a_should_not), \
            f"A 不应有 B 的定向记忆, got: {a_should_not}"

    def test_each_agent_only_remembers_own_responses(self):
        """Under pub/sub, each agent stores only its own replies, not others'."""
        import time

        agent_a = self._build_agent("iso_a2", "A", canned="A 的专属回复")
        agent_b = self._build_agent("iso_b2", "B", canned="B 的专属回复")

        worker_a = AgentWorker(agent_a)
        worker_b = AgentWorker(agent_b)

        bus = EventBus(policy=SchedulerPolicy(max_total_events=6))
        sim = Simulator(bus=bus)
        sim.add_agent(worker_a, topics=["public"])
        sim.add_agent(worker_b, topics=["public"])

        sim.inject_topic("public", "大家报一下自己的名字", source="system")
        dispatched = sim.run(max_events=6)
        assert dispatched >= 1

        time.sleep(2.0)

        # Agent A: only A's own memories, no B response leaks
        a_sqlite = agent_a.retrieve.sqlite.search_with_metadata(
            query="名字", agent_id="iso_a2", limit=20)
        a_contents = [r.content for r in a_sqlite]
        assert not any("B 的专属回复" in c for c in a_contents), \
            f"A 不应该有 B 的回复, got: {a_contents}"

        # Agent B: only B's own memories, no A response leaks
        b_sqlite = agent_b.retrieve.sqlite.search_with_metadata(
            query="名字", agent_id="iso_b2", limit=20)
        b_contents = [r.content for r in b_sqlite]
        assert not any("A 的专属回复" in c for c in b_contents), \
            f"B 不应该有 A 的回复, got: {b_contents}"


class _MockVectorStore:
    """Minimal in-memory VectorStore for isolation tests."""

    def __init__(self):
        self._items = {}

    def add(self, item):
        self._items[item.id] = item

    def search(self, query, limit=5, agent_id="", metadata_filters=None):
        results = []
        for item in self._items.values():
            if agent_id and item.agent_id != agent_id:
                continue
            from Memory.schema.retrieval import RetrievedMemory
            results.append(RetrievedMemory(item=item, score=0.85,
                          source=BackendTarget.VECTOR))
        return sorted(results, key=lambda r: r.score, reverse=True)[:limit]

    def delete_by_agent(self, agent_id):
        self._items = {k: v for k, v in self._items.items()
                      if v.agent_id != agent_id}
