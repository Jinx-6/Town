"""TaskCoordinator: deterministic + LLM multi-agent task orchestration."""
import json
import logging
from typing import Dict, List, Optional
from uuid import uuid4

from agents.event import Event
from agents.collab_schema import (
    CollaborationTask, TaskStatus, AgentCapability,
    TASK_ASSIGNED, TASK_DONE,
)

logger = logging.getLogger("TaskCoordinator")


class TaskCoordinator:
    """Orchestrates task creation, role-based assignment, and result collection.

    Subscribes to the EventBus on a collaboration topic to receive
    ``task_done`` events.  All methods are synchronous or async callbacks
    — the caller controls the event loop via
    ``event_bus.run_until_idle()``.

    When ``llm_client`` is provided, ``decompose_with_llm()`` and
    ``synthesize_results()`` become available for LLM-driven orchestration.
    """

    def __init__(self, event_bus, collab_topic: str = "town.collab",
                 coordinator_id: str = "coordinator",
                 llm_client=None):
        self.event_bus = event_bus
        self.topic = collab_topic
        self.coordinator_id = coordinator_id
        self.llm = llm_client
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

    # ── LLM orchestration ─────────────────────────────────

    async def decompose_with_llm(
        self,
        complex_request: str,
        available_roles: List[str],
        parent_task_id: str = "",
    ) -> List[CollaborationTask]:
        """Decompose a complex request into subtasks using LLM.

        Returns a list of ``CollaborationTask`` objects (empty on failure).
        Each subtask's ``required_role`` is validated against
        ``available_roles`` — unknown roles are skipped.
        """
        if not self.llm:
            raise RuntimeError("LLM client not configured")

        system_prompt = (
            "你是一个任务分解专家。将用户提出的复杂需求分解为多个子任务，"
            "分配给不同角色的团队成员完成。\n\n"
            f"可用角色：{'、'.join(available_roles)}\n\n"
            "严格按以下 JSON 格式输出，不要输出任何其他内容：\n"
            "{\n"
            '  "subtasks": [\n'
            "    {\n"
            '      "title": "子任务标题",\n'
            '      "description": "详细的任务描述，包含具体要求和预期产出",\n'
            '      "required_role": "必须从可用角色中选择"\n'
            "    }\n"
            "  ]\n"
            "}"
        )
        messages = [{"role": "user", "content": complex_request}]

        try:
            raw = await self.llm.generate(system_prompt, messages)
            text = self._extract_text(raw)
            data = self._parse_json(text)
            subtasks = data.get("subtasks", [])
            if not isinstance(subtasks, list):
                return []
        except Exception:
            logger.warning("LLM decomposition failed, returning empty list", exc_info=True)
            return []

        tasks = []
        for st in subtasks:
            if not isinstance(st, dict):
                continue
            if not all(k in st for k in ("title", "description", "required_role")):
                continue
            role = st["required_role"]
            if role not in available_roles:
                continue
            task = self.create_task(
                title=st["title"],
                description=st["description"],
                required_role=role,
                parent_task_id=parent_task_id,
            )
            tasks.append(task)

        return tasks

    async def synthesize_results(
        self,
        parent_task_id: str,
        original_request: str = "",
    ) -> str:
        """Synthesize completed subtask results into a final summary using LLM.

        Falls back to manual concatenation if the LLM call fails.
        """
        if not self.llm:
            raise RuntimeError("LLM client not configured")

        results = self._results.get(parent_task_id, [])
        if not results:
            return ""

        # Build per-agent contribution lines, enriching with task metadata
        parts = []
        for r in results:
            tid = r.get("task_id", "")
            task = self._tasks.get(tid)
            role = r.get("role", "")
            if not role and task:
                role = task.required_role
            agent = r.get("agent_id", "unknown")
            agent_name = r.get("agent_name", agent)
            result_text = self._extract_text(r.get("result", ""))
            parts.append(f"- [{role}] {agent_name}: {result_text}")

        contributions = "\n".join(parts)

        system_prompt = (
            "你是一个团队协作总结专家。将以下团队成员的工作结果汇总为一份完整的方案。\n\n"
            f"原始需求：{original_request}\n\n"
            "团队成员贡献：\n"
            f"{contributions}\n\n"
            "请整合以上内容，输出一份结构化的完整方案。"
        )
        messages = [{"role": "user", "content": "请汇总以上团队成员的贡献。"}]

        try:
            raw = await self.llm.generate(system_prompt, messages)
            return self._extract_text(raw).strip()
        except Exception:
            logger.warning("LLM synthesis failed, using fallback", exc_info=True)
            return "\n\n".join(parts)

    # ── helpers ───────────────────────────────────────────

    @staticmethod
    def _extract_text(llm_response) -> str:
        """Extract text from various LLM response shapes."""
        if isinstance(llm_response, str):
            return llm_response
        if isinstance(llm_response, dict):
            return str(llm_response.get("text", "") or "")
        if hasattr(llm_response, "text"):
            return str(getattr(llm_response, "text", ""))
        return str(llm_response)

    @staticmethod
    def _parse_json(text: str) -> dict:
        """Parse JSON from LLM output, stripping code fences if present."""
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        return json.loads(text)

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
