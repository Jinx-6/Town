"""TaskCoordinator: deterministic multi-agent task orchestration (no LLM)."""
from typing import Dict, List, Optional
from uuid import uuid4

from agents.event import Event
from agents.collab_schema import (
    CollaborationTask, TaskStatus, AgentCapability,
    TASK_ASSIGNED, TASK_DONE,
)


class TaskCoordinator:
    """Orchestrates task creation, role-based assignment, and result collection.

    Subscribes to the EventBus on a collaboration topic to receive
    ``task_done`` events.  All methods are synchronous or async callbacks
    — the caller controls the event loop via
    ``event_bus.run_until_idle()``.
    """

    def __init__(self, event_bus, collab_topic: str = "town.collab",
                 coordinator_id: str = "coordinator"):
        self.event_bus = event_bus
        self.topic = collab_topic
        self.coordinator_id = coordinator_id
        self._tasks: Dict[str, CollaborationTask] = {}
        # parent_task_id or task_id → list of result payloads
        self._results: Dict[str, list] = {}

        self.event_bus.subscribe(self.topic, self._on_collab_event)

    # ── public API ────────────────────────────────────────

    def create_task(self, title: str, description: str,
                    required_role: str,
                    parent_task_id: str = "") -> CollaborationTask:
        """Create a collaboration task and store it."""
        task = CollaborationTask(
            task_id=str(uuid4())[:8],
            title=title,
            description=description,
            required_role=required_role,
            parent_task_id=parent_task_id,
        )
        self._tasks[task.task_id] = task
        return task

    def assign_task(self, task: CollaborationTask, agent_id: str) -> None:
        """Assign a task to a specific agent.

        Publishes a ``task_assigned`` event to the EventBus queue
        (synchronous enqueue — execution happens when the bus runs).
        """
        task.status = TaskStatus.ASSIGNED
        task.assigned_to = agent_id

        event = Event(
            source_agent_id=self.coordinator_id,
            topic=self.topic,
            type=TASK_ASSIGNED,
            content=task.description,
            target_agent_id=agent_id,
            metadata={
                "task_id": task.task_id,
                "parent_task_id": task.parent_task_id,
                "required_role": task.required_role,
            },
        )
        self.event_bus.publish(event)

    def assign_by_role(self, task: CollaborationTask,
                       agents: Dict[str, AgentCapability]) -> bool:
        """Assign a task to the first agent matching ``required_role``.

        Returns True if an agent was found and assigned, False otherwise
        (task is marked FAILED).
        """
        for agent_id, cap in agents.items():
            if cap.role == task.required_role:
                self.assign_task(task, agent_id)
                return True

        task.status = TaskStatus.FAILED
        return False

    def collect_results(self, parent_task_id: str = "") -> List[dict]:
        """Non-blocking: return completed results for a result group.

        The result group key is ``parent_task_id or task_id`` — callers
        should pass the same ``parent_task_id`` used when creating
        subtasks.  Call ``event_bus.run_until_idle()`` first to process
        pending ``task_done`` events.
        """
        return list(self._results.get(parent_task_id, []))

    # ── internal ──────────────────────────────────────────

    async def _on_collab_event(self, event: Event) -> None:
        """EventBus subscription handler for collaboration events."""
        # Only process task_done events
        if event.type != TASK_DONE:
            return

        # Respect target_agent_id if set
        if event.target_agent_id and event.target_agent_id != self.coordinator_id:
            return

        task_id = event.metadata.get("task_id", "")
        if not task_id:
            return

        # Update task status
        task = self._tasks.get(task_id)
        if task and task.status == TaskStatus.ASSIGNED:
            task.status = TaskStatus.DONE
            task.result = event.content

        # Collect into result group
        parent_id = event.metadata.get("parent_task_id", "")
        result_group_id = parent_id or task_id
        self._results.setdefault(result_group_id, []).append({
            "task_id": task_id,
            "agent_id": event.source_agent_id,
            "result": event.content,
        })

        return None
