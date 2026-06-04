"""Collaboration data models for multi-agent task coordination."""
from dataclasses import dataclass, field
from enum import Enum


class TaskStatus(str, Enum):
    CREATED = "created"
    ASSIGNED = "assigned"
    DONE = "done"
    FAILED = "failed"


@dataclass
class CollaborationTask:
    task_id: str
    title: str
    description: str
    required_role: str
    parent_task_id: str = ""
    assigned_to: str = ""
    status: TaskStatus = TaskStatus.CREATED
    result: str = ""


@dataclass
class AgentCapability:
    agent_id: str
    role: str


TASK_ASSIGNED = "task_assigned"
TASK_DONE = "task_done"
