# @Time    :2026/4/18 21:10
# @Author  :jinxi
# @File    :router.py
# @Software:PyCharm

from typing import Optional
from ..schema.memory_item import MemoryItem
from ..schema.routing import RoutingDecision, BackendTarget, IntentType

class WriteRouter:
    """
    Smart routing layer: decides which backends to write to based on intent type.
    Supports IntentType-driven routing + legacy trigger-word fallback.
    """

    def __init__(self):
        self.graph_triggers = [
            "喜欢", "讨厌", "爱", "恨",
            "是", "叫", "属于",
            "在", "去", "搬", "住",
            "爸爸", "妈妈", "老婆", "老公", "朋友", "同事", "狗", "猫",
            "买", "卖", "想"
        ]

    def route(self, item: MemoryItem,
              intent_type: Optional[IntentType] = None) -> RoutingDecision:
        if intent_type is not None:
            return self._route_by_intent(item, intent_type)
        return self._route_by_triggers(item)

    def _route_by_intent(self, item: MemoryItem, intent: IntentType) -> RoutingDecision:
        if intent == IntentType.FACT_STATEMENT:
            targets = [BackendTarget.CACHE, BackendTarget.SQLITE, BackendTarget.VECTOR]
            reason = "fact_statement: full persist (cache+log+vector)"
            if item.metadata.entities or len(item.content.strip()) > 10:
                targets.append(BackendTarget.GRAPH)
                reason += ", trigger graph async extraction"
            return RoutingDecision(targets=targets, reason=reason, intent_type=intent)

        elif intent == IntentType.TASK_INSTRUCTION:
            return RoutingDecision(
                targets=[BackendTarget.CACHE, BackendTarget.SQLITE],
                reason="task_instruction: working memory + SQLite fallback",
                intent_type=intent
            )

        elif intent == IntentType.QA_QUERY:
            return RoutingDecision(
                targets=[BackendTarget.CACHE, BackendTarget.SQLITE],
                reason="qa_query: cache + interaction memory persist (query type)",
                intent_type=intent
            )

        elif intent == IntentType.CHITCHAT:
            return RoutingDecision(
                targets=[BackendTarget.CACHE],
                reason="chitchat: short-term cache only, no persist",
                intent_type=intent
            )

    def _route_by_triggers(self, item: MemoryItem) -> RoutingDecision:
        targets = [BackendTarget.CACHE, BackendTarget.SQLITE]
        content = item.content.strip()

        if len(content) < 4:
            return RoutingDecision(
                targets=targets,
                reason="low-info content, cache + sqlite only"
            )

        targets.append(BackendTarget.VECTOR)
        reason = "standard content: cache + log + vector"

        if any(trigger in content for trigger in self.graph_triggers):
            targets.append(BackendTarget.GRAPH)
            reason = "detected potential entity relations, adding graph"

        return RoutingDecision(targets=targets, reason=reason)
