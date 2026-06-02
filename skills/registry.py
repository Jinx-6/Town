"""
SkillRegistry — 技能注册与调度中心。
"""
from typing import Dict, List, Optional

from skills.base import Skill, SkillResult


class SkillRegistry:
    def __init__(self):
        self._skills: Dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        self._skills[skill.name] = skill

    def register_all(self, skills: list) -> None:
        for s in skills:
            self.register(s)

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def list_all(self) -> List[Skill]:
        return list(self._skills.values())

    def list_enabled(self) -> List[Skill]:
        return [s for s in self._skills.values() if s.enabled]

    async def run_if_applicable(self, event, agent) -> List[SkillResult]:
        results: List[SkillResult] = []
        for skill in self.list_enabled():
            if not skill.applies_to(event):
                continue
            try:
                result = await skill.run(event, agent)
                if result is not None:
                    results.append(result)
            except Exception as e:
                results.append(SkillResult(
                    success=False, error=f"[{skill.name}] {e}"
                ))
        return results

    def __len__(self) -> int:
        return len(self._skills)

    def __contains__(self, name: str) -> bool:
        return name in self._skills
