"""
In-process EventBus with SchedulerPolicy for multi-agent pub-sub.
"""
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Callable, Awaitable, Optional, List, Dict

from agents.event import Event

# async (Event) -> Optional[List[Event]]
Handler = Callable[[Event], Awaitable[Optional[List[Event]]]]


@dataclass
class SchedulerPolicy:
    max_total_events: int = 100


class EventBus:
    def __init__(self, policy: Optional[SchedulerPolicy] = None):
        self._policy = policy or SchedulerPolicy()
        self._subscriptions: Dict[str, List[Handler]] = defaultdict(list)
        self._queue: "deque[Event]" = deque()
        self.total_dispatched: int = 0

    def subscribe(self, topic: str, handler: Handler) -> None:
        self._subscriptions[topic].append(handler)

    def publish(self, event: Event) -> None:
        self._queue.append(event)

    @property
    def queue_size(self) -> int:
        return len(self._queue)

    async def dispatch_next(self) -> int:
        if not self._queue:
            return 0
        if self.total_dispatched >= self._policy.max_total_events:
            return 0

        event = self._queue.popleft()
        self.total_dispatched += 1

        handlers = self._subscriptions.get(event.topic, [])
        new_events: List[Event] = []

        for handler in handlers:
            try:
                result = await handler(event)
            except Exception:
                continue
            if result:
                new_events.extend(result)

        for e in new_events:
            self._queue.append(e)

        return len(handlers)

    async def run_until_idle(self, max_events: Optional[int] = None) -> int:
        if max_events is not None:
            self._policy.max_total_events = max_events
        self.total_dispatched = 0
        while self._queue and self.total_dispatched < self._policy.max_total_events:
            dispatched = await self.dispatch_next()
            if dispatched == 0 and not self._queue:
                break
        return self.total_dispatched
