"""Integration tests: RelationshipManager wired into Simulator + AgentWorker."""
import asyncio

from agents.event import Event
from agents.agent_worker import AgentWorker
from agents.memory_aware_agent import MemoryAwareAgent
from agents.agent_factory import setup_memory
from Memory.processor.intent_classifier import create_intent_classifier
from Memory.processor.assembler import PromptAssembler
from relationship import RelationshipManager
from simulator import Simulator


class FakeSentimentLLM:
    """Returns a parseable sentiment score, matching LLMClient interface."""

    def __init__(self, score: str = "2"):
        self._score = score
        self.call_count = 0

    async def generate(self, system_prompt: str, messages: list) -> str:
        self.call_count += 1
        return self._score

    async def generate_with_tools(self, system_prompt, messages, tools):
        return {"text": "", "tool_calls": [], "finish_reason": "stop"}


class FakeAgentLLM:
    """Canned-response LLM for MemoryAwareAgent in tests."""

    def __init__(self, reply: str = "test reply"):
        self._reply = reply

    async def generate(self, system_prompt: str, messages: list) -> str:
        return self._reply

    async def generate_with_tools(self, system_prompt, messages, tools):
        return {"text": self._reply, "tool_calls": [], "finish_reason": "stop"}


def _make_agent(agent_id: str, agent_name: str = "TestAgent",
                agent_role: str = "tester"):
    """Create a minimal MemoryAwareAgent for integration tests."""
    ingest, retrieve, cache, sqlite, vector_store = setup_memory(agent_id)
    classifier = create_intent_classifier(use_llm=False)
    assembler = PromptAssembler(agent_name=agent_name)
    return MemoryAwareAgent(
        agent_id=agent_id,
        agent_name=agent_name,
        agent_role=agent_role,
        ingest_hub=ingest,
        retrieve_hub=retrieve,
        intent_classifier=classifier,
        prompt_assembler=assembler,
        llm_client=FakeAgentLLM(reply=f"[{agent_name}] reply"),
    )


# ── Test 1 ───────────────────────────────────────────────

def test_simulator_injects_relationship_manager_into_worker():
    """Simulator holding a RelationshipManager injects it into AgentWorker on add_agent."""
    rm = RelationshipManager(llm_client=FakeSentimentLLM(score="2"))
    sim = Simulator(relationship_manager=rm)

    agent = _make_agent("agent_1", "Agent1")
    worker = AgentWorker(agent)  # no explicit relationship_manager

    sim.add_agent(worker, topics=["test"])

    assert worker.relationship_manager is rm


# ── Test 2 ───────────────────────────────────────────────

def test_agent_direct_message_updates_relationship():
    """Agent-to-agent direct message triggers affinity update in RelationshipManager."""
    rm = RelationshipManager(llm_client=FakeSentimentLLM(score="5"))

    agent_a = _make_agent("agent_a", "AgentA")
    agent_b = _make_agent("agent_b", "AgentB")

    worker_a = AgentWorker(agent_a, relationship_manager=rm)

    # Direct message from B → A (targeted)
    event = Event(
        source_agent_id="agent_b",
        target_agent_id="agent_a",
        topic="town.direct",
        type="message",
        content="你好 AgentA，今天过得怎么样？",
    )

    asyncio.run(worker_a.handle_event(event))

    info = rm.get_affinity_info("agent_a", "agent_b")
    assert info["score"] > 0, f"Expected positive score after friendly message, got {info}"


# ── Test 3 ───────────────────────────────────────────────

def test_system_message_does_not_update_relationship():
    """System-sourced events must not trigger relationship updates."""
    sentiment_llm = FakeSentimentLLM(score="5")
    rm = RelationshipManager(llm_client=sentiment_llm)

    agent = _make_agent("agent_sys", "AgentSys")
    worker = AgentWorker(agent, relationship_manager=rm)

    event = Event(
        source_agent_id="system",
        target_agent_id="agent_sys",
        topic="town.public",
        type="system",
        content="系统公告：服务器将在 5 分钟后重启。",
    )

    asyncio.run(worker.handle_event(event))

    # System source should be excluded by _should_update_relationship
    assert sentiment_llm.call_count == 0

    info = rm.get_affinity_info("agent_sys", "system")
    assert info["score"] == 10  # default initial score, unchanged


# ── Test 4 ───────────────────────────────────────────────

def test_user_message_does_not_update_relationship():
    """User-sourced events must not trigger relationship updates."""
    sentiment_llm = FakeSentimentLLM(score="5")
    rm = RelationshipManager(llm_client=sentiment_llm)

    agent = _make_agent("agent_user_test", "AgentUser")
    worker = AgentWorker(agent, relationship_manager=rm)

    event = Event(
        source_agent_id="user",
        target_agent_id="agent_user_test",
        topic="town.public",
        type="message",
        content="你好，Agent！",
    )

    asyncio.run(worker.handle_event(event))

    # User source should be excluded by _should_update_relationship
    assert sentiment_llm.call_count == 0

    info = rm.get_affinity_info("agent_user_test", "user")
    assert info["score"] == 10  # default initial score, unchanged
