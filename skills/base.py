"""
Skill — 面向任务的能力封装。

Tool 是原子函数；Skill 是 Task-level 的能力单元，
可以调用 Tool、读写 Memory、调用 LLM 等。
"""
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class SkillResult:
    success: bool
    data: Any = None
    error: str = ""


class Skill:
    name: str = ""
    description: str = ""
    enabled: bool = True

    def applies_to(self, event) -> bool:
        return True

    async def run(self, event, agent) -> SkillResult:
        raise NotImplementedError
