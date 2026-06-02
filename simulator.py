"""
Simulator orchestrates the multi-agent publish-subscribe runtime.
"""
import asyncio
from typing import Dict, List, Optional

from agents.event import Event
from agents.event_bus import EventBus, SchedulerPolicy
from agents.agent_worker import AgentWorker


class Simulator:
    def __init__(self, bus: Optional[EventBus] = None,
                 policy: Optional[SchedulerPolicy] = None):
        self.bus = bus or EventBus(policy=policy)
        self._workers: Dict[str, AgentWorker] = {}

    def add_agent(self, worker: AgentWorker, topics: List[str]) -> None:
        agent_id = worker.agent.agent_id
        self._workers[agent_id] = worker
        for topic in topics:
            self.bus.subscribe(topic, worker.handle_event)

    def inject_topic(self, topic: str, content: str,
                     source: str = "system") -> None:
        event = Event(
            source_agent_id=source,
            topic=topic,
            type="system",
            content=content,
        )
        self.bus.publish(event)

    def run(self, max_events: Optional[int] = None) -> int:
        return asyncio.run(self.bus.run_until_idle(max_events))

    async def run_async(self, max_events: Optional[int] = None) -> int:
        return await self.bus.run_until_idle(max_events)

    def summary(self) -> Dict[str, int]:
        return {aid: w.processed_count for aid, w in self._workers.items()}
