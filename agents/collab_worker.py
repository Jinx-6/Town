"""CollabAgentWorker: extends AgentWorker with task execution support."""
from typing import List, Optional

from agents.event import Event
from agents.agent_worker import AgentWorker
from agents.collab_schema import TASK_ASSIGNED, TASK_DONE


class CollabAgentWorker(AgentWorker):
    """AgentWorker that can execute collaboration tasks.

    Intercepts ``task_assigned`` events directed at this agent,
    executes them via ``agent.respond()``, and returns a ``task_done``
    event.  ``task_done`` events are filtered to prevent event loops.
    Normal conversation events fall through to the parent class.
    """

    async def handle_event(self, event: Event) -> Optional[List[Event]]:
        # Guard: task_done events must never be treated as conversation.
        if event.type == TASK_DONE:
            return None

        # Intercept task_assigned directed at this agent.
        if event.type == TASK_ASSIGNED:
            if event.target_agent_id == self.agent.agent_id:
                return await self._handle_task(event)
            # Not for me — ignore.
            return None

        # Normal conversation → parent logic.
        return await super().handle_event(event)

    async def _handle_task(self, event: Event) -> Optional[List[Event]]:
        """Execute the assigned task and return a task_done event."""
        resp = await self.agent.respond(event.content)
        if resp.error:
            return None

        reply = Event(
            source_agent_id=self.agent.agent_id,
            topic=event.topic,
            type=TASK_DONE,
            content=resp.text,
            target_agent_id=event.source_agent_id,
            metadata={
                "task_id": event.metadata.get("task_id", ""),
                "parent_task_id": event.metadata.get("parent_task_id", ""),
                "agent_name": self.agent.agent_name,
                "in_reply_to": event.event_id,
            },
        )
        return [reply]
