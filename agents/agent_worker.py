"""
AgentWorker wraps a MemoryAwareAgent as a pub-sub event subscriber.
"""
from typing import List, Optional

from agents.event import Event


class AgentWorker:
    def __init__(self, agent, relationship_manager=None):
        self.agent = agent
        self.relationship_manager = relationship_manager
        self.processed_count: int = 0

    async def handle_event(self, event: Event) -> Optional[List[Event]]:
        if event.source_agent_id == self.agent.agent_id:
            return None

        if event.target_agent_id and event.target_agent_id != self.agent.agent_id:
            return None

        self.processed_count += 1

        try:
            resp = await self.agent.respond(event.content)
        except Exception:
            return None

        if resp.error:
            return None

        # Agent-to-agent direct message: update relationship affinity.
        # We reuse the NPC/player affinity API: the current agent acts as
        # the NPC whose affinity toward the sending agent (player) is updated.
        if self._should_update_relationship(event):
            try:
                await self.relationship_manager.update_affinity(
                    npc_id=self.agent.agent_id,
                    player_name=event.source_agent_id,
                    player_message=event.content,
                    npc_reply=resp.text,
                )
            except Exception:
                pass

        reply = Event(
            source_agent_id=self.agent.agent_id,
            topic=event.topic,
            type="message",
            content=resp.text,
            metadata={
                "in_reply_to": event.event_id,
                "agent_name": self.agent.agent_name,
            },
        )
        return [reply]

    def _should_update_relationship(self, event: Event) -> bool:
        """Only update relationship for explicit agent-to-agent direct messages."""
        return (
            self.relationship_manager is not None
            and event.source_agent_id is not None
            and event.source_agent_id not in {"system", "user"}
            and event.source_agent_id != self.agent.agent_id
            and event.target_agent_id == self.agent.agent_id
        )
