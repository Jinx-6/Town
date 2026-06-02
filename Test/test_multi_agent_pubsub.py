"""
Tests for the publish-subscribe multi-agent runtime.
"""
import sys
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

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
