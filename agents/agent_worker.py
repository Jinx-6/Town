"""
AgentWorker wraps a MemoryAwareAgent as a pub-sub event subscriber.
"""
from typing import List, Optional

from agents.event import Event


class AgentWorker:
    def __init__(self, agent):
        self.agent = agent
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
