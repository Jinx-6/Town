# @Time    :2026/5/12 18:30 (进喜升级版)
# @Author  :进喜
# @File    :ingest.py

from typing import Dict, Any, Optional, List
from ..schema.memory_item import MemoryItem, MemoryRole, MemoryStage, MemoryMetadata
from ..schema.routing import BackendTarget, IntentType
from ..schema.events import MemoryWriteEvent
from ..processor.router import WriteRouter
from ..processor.metadata_extractor import extract_metadata
from ..hub.async_dispatcher import AsyncDispatcher
from ..storage.working_cache import WorkingMemoryCache


class IngestHub:
    """
    核心写入入口 (策略感知版)
    现在的“前台收银员”不仅负责记账，还会给每条信息打上“可靠性标签”，
    以便后续的 UpdatePolicy 能根据置信度决定是覆写还是追加。
    """

    def __init__(
            self,
            working_cache: WorkingMemoryCache,
            router: WriteRouter,
            dispatcher: AsyncDispatcher
    ):
        self.cache = working_cache
        self.router = router
        self.dispatcher = dispatcher

    def process_message(
            self,
            content: str,
            role: MemoryRole,
            metadata: Optional[Dict[str, Any]] = None,
            confidence: float = 1.0,
            agent_id: str = "default",
            intent_type: Optional[str] = None
    ) -> MemoryItem:
        import time
        import uuid

        meta = metadata or {}
        if "confidence" not in meta:
            meta["confidence"] = confidence

        # 意图驱动的 metadata 设置（不覆盖用户显式传入）
        if intent_type and "memory_type" not in meta:
            _intent_mtype = {
                "fact_statement": "event",
                "task_instruction": "task",
                "qa_query": "query",
                "chitchat": "dialogue",
            }
            meta["memory_type"] = _intent_mtype.get(intent_type, "event")
            meta["is_factual_memory"] = intent_type == "fact_statement"

        try:
            extracted = extract_metadata(content, existing_meta=meta)
            for k, v in extracted.items():
                if k not in meta or meta.get(k) in (None, "", [], False):
                    meta[k] = v
        except Exception:
            pass

        item = MemoryItem(
            id=f"mem_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}",
            content=content,
            role=role,
            metadata=meta,
            stage=MemoryStage.SENSORY,
            agent_id=agent_id
        )

        # 意图驱动路由
        route_intent = IntentType(intent_type) if intent_type else None
        decision = self.router.route(item, intent_type=route_intent)

        if BackendTarget.CACHE in decision.targets:
            self.cache.add(item)

        async_targets = [t for t in decision.targets if t != BackendTarget.CACHE]
        if async_targets:
            write_event = MemoryWriteEvent(
                item=item,
                targets=async_targets,
                confidence=meta["confidence"]
            )
            self.dispatcher.publish(write_event)

        return item

    def ingest_structured_fact(
            self,
            triplets: List[Dict[str, str]],  # 格式如: [{"sub": "进喜", "pred": "居住在", "obj": "上海"}]
            agent_id: str,  # ✨ 核心修改：必须指定事实的归属者
            confidence: float = 0.9,
            source_msg_id: Optional[str] = None
    ):
        """
        ✨ 结构化事实注入接口
        当 LLM 从对话中提炼出图谱三元组时，直接触发此接口。
        """
        # 构造图谱更新事件，明确包含 agent_id
        fact_event = {
            "type": "GRAPH_FACT_UPDATE",
            "agent_id": agent_id,  # ✨ 确保图谱更新时只在特定居民的子图里操作
            "data": triplets,
            "confidence": confidence,
            "source": source_msg_id
        }

        print(f"📊 [IngestHub] 正在为居民 [{agent_id}] 注入 {len(triplets)} 条结构化知识")

        # 这里的 dispatcher 会把带有 agent_id 的事件推送给负责图谱的 WriteHub/UpdatePolicy
        self.dispatcher.publish_custom_event(fact_event)