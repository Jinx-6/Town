"""
Event dataclass for the publish-subscribe multi-agent runtime.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict
import uuid


@dataclass
class Event:
    source_agent_id: str
    topic: str
    type: str = "message"
    content: str = ""
    target_agent_id: str = ""
    event_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)
