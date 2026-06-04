"""Integration tests: TaskCoordinator + CollabAgentWorker (Phase M2-a)."""
import asyncio
from unittest.mock import MagicMock

from agents.event import Event
from agents.event_bus import EventBus, SchedulerPolicy
from agents.agent_factory import create_memory_aware_agent
from agents.collab_worker import CollabAgentWorker
from agents.task_coordinator import TaskCoordinator
from agents.collab_schema import (
    CollaborationTask, AgentCapability, TaskStatus,
    TASK_ASSIGNED, TASK_DONE,
)


class FakeLLM:
    """Canned-response LLM — no external dependencies."""

    def __init__(self, reply: str = "task completed"):
        self._reply = reply
        self.call_count = 0

    async def generate(self, system_prompt: str, messages: list) -> str:
        self.call_count += 1
        return self._reply

    async def generate_with_tools(self, system_prompt, messages, tools):
        return {"text": "", "tool_calls": [], "finish_reason": "stop"}


def _make_agent(agent_id, agent_name="TestAgent", agent_role="tester"):
    """Create a minimal MemoryAwareAgent for tests."""
    agent, _c, _s, _v = create_memory_aware_agent(
        agent_id=agent_id, agent_name=agent_name, agent_role=agent_role,
        llm_client=FakeLLM(reply=f"[{agent_name}] done"),
    )
    return agent


def _make_event(**overrides):
    """Construct an Event with sensible defaults for collaboration tests."""
    defaults = {
        "source_agent_id": "coordinator",
        "topic": "town.collab",
        "type": TASK_ASSIGNED,
        "content": "fix the server",
        "target_agent_id": "agent_zhang",
        "metadata": {
            "task_id": "t001",
            "parent_task_id": "",
            "required_role": "engineer",
        },
    }
    defaults.update(overrides)
    return Event(**defaults)


# ── Test 1 ───────────────────────────────────────────────

def test_task_creation_and_assignment():
    """Task created and assigned: status updated, event published to bus."""
    bus = EventBus()
    coordinator = TaskCoordinator(bus, collab_topic="town.collab")

    task = coordinator.create_task(
        title="fix bug", description="repair server",
        required_role="engineer",
    )
    assert task.task_id
    assert task.status == TaskStatus.CREATED

    coordinator.assign_task(task, "agent_zhang")

    assert task.status == TaskStatus.ASSIGNED
    assert task.assigned_to == "agent_zhang"
    assert bus.queue_size >= 1


# ── Test 2 ───────────────────────────────────────────────

def test_agent_executes_assigned_task():
    """CollabAgentWorker executes a task_assigned event and returns task_done."""
    agent = _make_agent("agent_zhang", "Zhang", "engineer")
    worker = CollabAgentWorker(agent)

    event = _make_event(
        type=TASK_ASSIGNED,
        target_agent_id="agent_zhang",
        content="repair the server",
        metadata={"task_id": "t001", "parent_task_id": "p1", "required_role": "engineer"},
    )

    result = asyncio.run(worker.handle_event(event))

    assert result is not None, "Worker should return a reply list"
    assert len(result) == 1
    reply = result[0]
    assert reply.type == TASK_DONE
    assert reply.source_agent_id == "agent_zhang"
    assert reply.target_agent_id == "coordinator"
    assert "done" in reply.content
    assert reply.metadata["task_id"] == "t001"
    assert reply.metadata["parent_task_id"] == "p1"


# ── Test 3 ───────────────────────────────────────────────

def test_coordinator_collects_results():
    """Coordinator collects task_done results after agents execute subtasks."""
    bus = EventBus(policy=SchedulerPolicy(max_total_events=20))
    coordinator = TaskCoordinator(bus, collab_topic="town.collab")

    # Create two sub-tasks under a common parent
    parent_id = "parent_001"
    t1 = coordinator.create_task("t1", "task one desc", "engineer", parent_id)
    t2 = coordinator.create_task("t2", "task two desc", "pm", parent_id)

    # Create agents and CollabAgentWorkers
    agent_a = _make_agent("agent_a", "AgentA", "engineer")
    agent_b = _make_agent("agent_b", "AgentB", "pm")
    worker_a = CollabAgentWorker(agent_a)
    worker_b = CollabAgentWorker(agent_b)

    bus.subscribe("town.collab", worker_a.handle_event)
    bus.subscribe("town.collab", worker_b.handle_event)

    # Assign and run
    coordinator.assign_task(t1, "agent_a")
    coordinator.assign_task(t2, "agent_b")

    asyncio.run(bus.run_until_idle(max_events=20))

    results = coordinator.collect_results(parent_id)
    assert len(results) == 2, f"Expected 2 results, got {len(results)}"

    task_ids = {r["task_id"] for r in results}
    assert t1.task_id in task_ids
    assert t2.task_id in task_ids


# ── Test 4 ───────────────────────────────────────────────

def test_agent_ignores_unassigned_task():
    """CollabAgentWorker ignores task_assigned events targeting a different agent."""
    agent = _make_agent("agent_zhang", "Zhang", "engineer")
    worker = CollabAgentWorker(agent)

    event = _make_event(
        type=TASK_ASSIGNED,
        target_agent_id="agent_li",  # different agent
        content="do something",
    )

    result = asyncio.run(worker.handle_event(event))

    assert result is None, "Task for another agent should be ignored"


# ── Test 5 ───────────────────────────────────────────────

def test_unmatched_role_task_fails():
    """assign_by_role fails when no agent matches the required role."""
    bus = EventBus()
    coordinator = TaskCoordinator(bus)

    task = coordinator.create_task(
        title="design UI", description="redesign dashboard",
        required_role="designer",
    )

    agents = {
        "agent_a": AgentCapability(agent_id="agent_a", role="engineer"),
        "agent_b": AgentCapability(agent_id="agent_b", role="pm"),
    }

    ok = coordinator.assign_by_role(task, agents)

    assert ok is False
    assert task.status == TaskStatus.FAILED
    assert task.assigned_to == ""


# ── Test 6 ───────────────────────────────────────────────

def test_task_done_updates_coordinator_store():
    """Coordinator's subscription handler updates task status on task_done."""
    bus = EventBus(policy=SchedulerPolicy(max_total_events=10))
    coordinator = TaskCoordinator(bus, collab_topic="town.collab")

    task = coordinator.create_task("t_up", "update test", "engineer")
    agent = _make_agent("agent_up", "AgentUp", "engineer")
    worker = CollabAgentWorker(agent)
    bus.subscribe("town.collab", worker.handle_event)

    coordinator.assign_task(task, "agent_up")
    asyncio.run(bus.run_until_idle(max_events=10))

    stored = coordinator._tasks.get(task.task_id)
    assert stored is not None
    assert stored.status == TaskStatus.DONE
    assert "done" in stored.result


# ── Test 7 ───────────────────────────────────────────────

def test_task_done_does_not_create_event_loop():
    """task_done events are filtered — no conversation reply, no infinite loop."""
    fake_llm = FakeLLM(reply="should not be called")
    agent = _make_agent("agent_loop", "AgentLoop", "engineer")
    # Override with our tracked FakeLLM
    agent.llm = fake_llm
    worker = CollabAgentWorker(agent)

    event = _make_event(
        type=TASK_DONE,
        source_agent_id="agent_other",
        target_agent_id="coordinator",
        content="some result",
        metadata={"task_id": "t999"},
    )

    result = asyncio.run(worker.handle_event(event))

    assert result is None, "task_done must not produce any reply"
    assert fake_llm.call_count == 0, "LLM should not be called for task_done"
