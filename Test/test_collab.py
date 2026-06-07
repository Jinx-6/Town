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
from Test.mocks import MockLLMClient


class FakeLLM:
    """Canned-response LLM — for tests that need call_count tracking."""

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
        llm_client=MockLLMClient(canned_response=f"[{agent_name}] done"),
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


# ── M2-b helpers ─────────────────────────────────────────

_THREE_SUBTASK_JSON = """{
  "subtasks": [
    {"title": "t1", "description": "write PRD", "required_role": "产品经理"},
    {"title": "t2", "description": "estimate effort", "required_role": "Python工程师"},
    {"title": "t3", "description": "design UI", "required_role": "UI设计师"}
  ]
}"""

_TWO_SUBTASK_JSON = """{
  "subtasks": [
    {"title": "spec", "description": "write spec", "required_role": "产品经理"},
    {"title": "impl", "description": "implement feature", "required_role": "Python工程师"}
  ]
}"""

_BAD_ROLE_JSON = """{
  "subtasks": [
    {"title": "t1", "description": "desc", "required_role": "产品经理"},
    {"title": "t2", "description": "desc", "required_role": "清洁工"}
  ]
}"""


class FakeDecomposeLLM:
    """Returns canned JSON for decomposition."""

    def __init__(self, json_str: str):
        self._json = json_str
        self.call_count = 0

    async def generate(self, system_prompt: str, messages: list) -> str:
        self.call_count += 1
        return self._json

    async def generate_with_tools(self, system_prompt, messages, tools):
        return {"text": "", "tool_calls": [], "finish_reason": "stop"}


class FakeCollabLLM:
    """Returns JSON for decompose and text for synthesize based on prompt."""

    def __init__(self, decompose_json: str, synthesize_text: str):
        self._decompose_json = decompose_json
        self._synthesize_text = synthesize_text
        self.decompose_count = 0
        self.synthesize_count = 0

    async def generate(self, system_prompt: str, messages: list) -> str:
        if "任务分解" in system_prompt:
            self.decompose_count += 1
            return self._decompose_json
        self.synthesize_count += 1
        return self._synthesize_text

    async def generate_with_tools(self, system_prompt, messages, tools):
        return {"text": "", "tool_calls": [], "finish_reason": "stop"}


def _make_coordinator(bus, llm_client=None, **kwargs):
    """Create a TaskCoordinator, optionally with an LLM."""
    return TaskCoordinator(bus, llm_client=llm_client, **kwargs)


# ── Test 8 ───────────────────────────────────────────────

def test_decompose_with_llm_returns_subtasks():
    """LLM decomposition returns correctly structured CollaborationTask objects."""
    bus = EventBus()
    fake_llm = FakeDecomposeLLM(_THREE_SUBTASK_JSON)
    coordinator = _make_coordinator(bus, llm_client=fake_llm)

    roles = ["产品经理", "Python工程师", "UI设计师"]
    tasks = asyncio.run(coordinator.decompose_with_llm(
        "设计用户登录页面", roles, parent_task_id="p1",
    ))

    assert len(tasks) == 3, f"Expected 3 tasks, got {len(tasks)}"
    assert tasks[0].required_role == "产品经理"
    assert tasks[1].required_role == "Python工程师"
    assert tasks[2].required_role == "UI设计师"
    for t in tasks:
        assert t.parent_task_id == "p1"
        assert t.status == TaskStatus.CREATED
    assert fake_llm.call_count == 1


# ── Test 9 ───────────────────────────────────────────────

def test_decompose_with_llm_bad_json_returns_empty():
    """Malformed LLM output → empty list, no exception."""
    bus = EventBus()
    coordinator = _make_coordinator(bus, llm_client=FakeDecomposeLLM("not valid!!"))

    tasks = asyncio.run(coordinator.decompose_with_llm(
        "request", ["engineer"],
    ))
    assert tasks == []


def test_decompose_with_llm_unknown_role_skipped():
    """Subtask with required_role not in available_roles is excluded."""
    bus = EventBus()
    coordinator = _make_coordinator(bus, llm_client=FakeDecomposeLLM(_BAD_ROLE_JSON))

    roles = ["产品经理", "Python工程师"]
    tasks = asyncio.run(coordinator.decompose_with_llm(
        "request", roles,
    ))

    assert len(tasks) == 1
    assert tasks[0].required_role == "产品经理"


def test_decompose_without_llm_raises():
    """decompose_with_llm without llm_client raises RuntimeError."""
    bus = EventBus()
    coordinator = _make_coordinator(bus, llm_client=None)

    try:
        asyncio.run(coordinator.decompose_with_llm("req", ["role"]))
        assert False, "Should have raised RuntimeError"
    except RuntimeError as e:
        assert "not configured" in str(e)


# ── Test 10 ──────────────────────────────────────────────

def test_synthesize_results_returns_summary():
    """synthesize_results calls LLM and returns summary text."""
    bus = EventBus()
    fake_llm = FakeDecomposeLLM("integrated final plan")
    coordinator = _make_coordinator(bus, llm_client=fake_llm)

    # Pre-populate results and tasks
    coordinator._results["p1"] = [
        {"task_id": "t1", "agent_id": "agent_a", "agent_name": "A", "result": "PRD done"},
        {"task_id": "t2", "agent_id": "agent_b", "agent_name": "B", "result": "code done"},
    ]
    coordinator._tasks["t1"] = CollaborationTask(
        task_id="t1", title="spec", description="d", required_role="产品经理",
    )
    coordinator._tasks["t2"] = CollaborationTask(
        task_id="t2", title="impl", description="d", required_role="Python工程师",
    )

    summary = asyncio.run(coordinator.synthesize_results("p1", "original request"))
    assert summary == "integrated final plan"
    assert fake_llm.call_count == 1


def test_synthesize_without_llm_raises():
    """synthesize_results without llm_client raises RuntimeError."""
    bus = EventBus()
    coordinator = _make_coordinator(bus, llm_client=None)

    try:
        asyncio.run(coordinator.synthesize_results("p1"))
        assert False, "Should have raised RuntimeError"
    except RuntimeError as e:
        assert "not configured" in str(e)


# ── Test 11 ──────────────────────────────────────────────

def test_e2e_collaboration_with_llm_orchestration():
    """Full pipeline: decompose → assign → execute → collect → synthesize."""
    bus = EventBus(policy=SchedulerPolicy(max_total_events=50))
    collab_llm = FakeCollabLLM(
        decompose_json=_TWO_SUBTASK_JSON,
        synthesize_text="final integrated plan",
    )
    coordinator = _make_coordinator(bus, llm_client=collab_llm)

    roles = ["产品经理", "Python工程师"]

    # Step 1: decompose
    tasks = asyncio.run(coordinator.decompose_with_llm(
        "plan a feature release", roles, parent_task_id="parent_e2e",
    ))
    assert len(tasks) == 2

    # Step 2: create agents + workers
    agent_pm = _make_agent("agent_pm", "LiPM", "产品经理")
    agent_eng = _make_agent("agent_eng", "ZhangEng", "Python工程师")
    worker_pm = CollabAgentWorker(agent_pm)
    worker_eng = CollabAgentWorker(agent_eng)
    bus.subscribe("town.collab", worker_pm.handle_event)
    bus.subscribe("town.collab", worker_eng.handle_event)

    # Step 3: assign by role
    agents_map = {
        "agent_pm": AgentCapability(agent_id="agent_pm", role="产品经理"),
        "agent_eng": AgentCapability(agent_id="agent_eng", role="Python工程师"),
    }
    for t in tasks:
        ok = coordinator.assign_by_role(t, agents_map)
        assert ok, f"Failed to assign task {t.task_id} ({t.required_role})"

    # Step 4: run event loop
    asyncio.run(bus.run_until_idle(max_events=50))

    # Step 5: verify results collected
    results = coordinator.collect_results("parent_e2e")
    assert len(results) == 2, f"Expected 2 results, got {len(results)}"

    # Step 6: verify task status
    for t in tasks:
        stored = coordinator._tasks.get(t.task_id)
        assert stored is not None
        assert stored.status == TaskStatus.DONE, f"Task {t.task_id} not DONE: {stored.status}"

    # Step 7: synthesize
    summary = asyncio.run(coordinator.synthesize_results(
        "parent_e2e", "plan a feature release",
    ))
    assert len(summary) > 0
    assert collab_llm.decompose_count == 1
    assert collab_llm.synthesize_count == 1
