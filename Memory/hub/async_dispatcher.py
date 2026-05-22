import threading
import queue
from typing import Dict, Any, Optional

# ✨ 引入全局事件总线契约
from ..schema.events import BaseMemoryEvent, EventType, MemoryWriteEvent, GraphExtractionEvent, \
    ConsolidationTriggerEvent
from ..schema.memory_item import MemoryItem, MemoryStage
from ..schema.routing import BackendTarget
# ✨ 引入新版冲突仲裁契约
from ..schema.conflict import ResolutionStrategy
from ..processor.extractor import GraphExtractor
from ..processor.conflict_resolver import ConflictResolver
from ..storage.index_manager import IndexManager
from ..policies.update_policy import UpdatePolicy, UpdateAction


class AsyncDispatcher:
    def __init__(
            self,
            backends: Dict[BackendTarget, Any],
            extractor: Optional[GraphExtractor] = None,
            conflict_resolver: Optional[ConflictResolver] = None,
            index_manager: Optional[IndexManager] = None,
            update_policy: Optional[UpdatePolicy] = None
    ):
        self.backends = backends
        self.extractor = extractor
        self.conflict_resolver = conflict_resolver
        self.index_manager = index_manager
        self.update_policy = update_policy or UpdatePolicy()

        self.consolidate_hub = None
        self.event_queue = queue.Queue(maxsize=2000)
        self.worker_thread = threading.Thread(target=self._event_loop, daemon=True)
        self.worker_thread.start()

    def register_consolidate_hub(self, consolidate_hub):
        self.consolidate_hub = consolidate_hub
        print("🔌 [Dispatcher] ConsolidateHub 已成功接入全域事件总线。")

    def publish(self, event: BaseMemoryEvent):
        try:
            self.event_queue.put(event, timeout=0.1)
        except queue.Full:
            print(f"⚠️ [Dispatcher] 事件总线已满，丢弃事件: {event.event_type}")

    def _event_loop(self):
        while True:
            try:
                event = self.event_queue.get()
                if event.event_type == EventType.MEMORY_WRITE:
                    self._handle_memory_write(event)
                elif event.event_type == EventType.GRAPH_EXTRACTION:
                    self._handle_graph_extraction(event)
                elif event.event_type == EventType.CONSOLIDATION_TRIGGER:
                    self._handle_consolidation(event)

            except Exception as e:
                # 🌟 完整的堆栈打印，以后再也不用瞎猜报错在哪一行了！
                import traceback
                print("\n" + "🔥" * 20 + " 致命错误完整追踪 " + "🔥" * 20)
                traceback.print_exc()
                print("🔥" * 58 + "\n")
                print(f"❌ [Dispatcher 致命错误] 事件执行异常: {e}")
            finally:
                self.event_queue.task_done()

    # ==========================================
    # 核心链路 1：处理快速写入事件
    # ==========================================
    def _handle_memory_write(self, event: MemoryWriteEvent):
        original_item = event.item
        targets = event.targets
        semantic_item_to_process = original_item.model_copy()

        # ✨ 提取出当前事件的主人公身份
        current_agent_id = original_item.agent_id

        # 1. 极速写入 L1 SQLite
        if BackendTarget.SQLITE in targets and BackendTarget.SQLITE in self.backends:
            sqlite_item = original_item.model_copy(update={"stage": MemoryStage.EPISODIC})
            self.backends[BackendTarget.SQLITE].add(sqlite_item)

        # 2. 冲突嗅探与 LLM 仲裁
        if self.conflict_resolver and self.index_manager and BackendTarget.VECTOR in self.backends:
            vector_store = self.backends[BackendTarget.VECTOR]
            # ✨ 修补 1：向量搜索必须带上 agent_id
            similar_hits = vector_store.search(query=original_item.content, agent_id=current_agent_id, limit=2)

            for hit in similar_hits:
                if hit.score > 0.8:
                    old_memory = hit.item
                    record = self.conflict_resolver.resolve(old_memory, original_item.content)

                    if record.has_conflict and record.strategy in [ResolutionStrategy.OVERWRITE,
                                                                   ResolutionStrategy.MERGE_NEW]:
                        strategy_str = getattr(record.strategy, 'value', record.strategy)
                        print(f"🛡️ [Dispatcher] 拦截冲突！策略: {strategy_str} | 推理: {record.reasoning}")

                        self.index_manager.delete_memory(old_memory.id)

                        if record.merged_content:
                            semantic_item_to_process = old_memory.model_copy(
                                update={
                                    "content": record.merged_content,
                                    "stage": MemoryStage.SEMANTIC
                                }
                            )
                        break

        # 3. 写入 L2 Vector Store
        if BackendTarget.VECTOR in targets and BackendTarget.VECTOR in self.backends:
            vector_item = semantic_item_to_process.model_copy(update={"stage": MemoryStage.SEMANTIC})
            self.backends[BackendTarget.VECTOR].add(vector_item)

        # 4. 架构魔法：异步图谱抽取
            # 4. 架构魔法：异步图谱抽取
            if BackendTarget.GRAPH in targets and self.extractor:
                # ✨ 这里的 GraphExtractionEvent 现在已经拥有 agent_id 字段
                graph_event = GraphExtractionEvent(
                    source_item_id=semantic_item_to_process.id,
                    raw_text=semantic_item_to_process.content,
                    agent_id=current_agent_id  # 👈 直接在初始化时传入，既严谨又安全
                )

                # 发布到总线，等待后续 _handle_graph_extraction 消费
                self.publish(graph_event)

    # ==========================================
    # 核心链路 2：处理慢速图谱抽取事件
    # ==========================================
    def _handle_graph_extraction(self, event: GraphExtractionEvent):
        if BackendTarget.GRAPH not in self.backends or not self.extractor:
            return

        # ✨ 获取上游传过来的身份ID，如果没有就用默认值防撞
        agent_id = event.agent_id

        extraction_result = self.extractor.extract(
            text=event.raw_text,
            user_name="用户",
            agent_name=agent_id  # 把图谱里提取的主体对应到当前的 agent_id
        )

        graph_db = self.backends[BackendTarget.GRAPH]
        confidence = getattr(extraction_result, 'confidence', 0.8)

        for triplet in extraction_result.triplets:
            try:
                # ✨ 修补 3：图谱搜索带上 agent_id
                existing_objects = graph_db.get_objects_for_predicate(
                    sub=triplet.subject,
                    pred=triplet.predicate,
                    agent_id=agent_id
                )

                if not existing_objects:
                    self._do_graph_write(graph_db, triplet, event.source_item_id, agent_id)
                    continue

                for old_obj in existing_objects:
                    if old_obj == triplet.object:
                        continue

                    action = self.update_policy.resolve_conflict(
                        existing_edge={"end": old_obj},
                        new_edge={"rel": triplet.predicate, "end": triplet.object},
                        new_confidence=confidence
                    )

                    # ✨ 图谱所有的删、改操作都带上 agent_id
                    if action == UpdateAction.OVERWRITE:
                        print(f"🔄 [Dispatcher] 知识覆写: {triplet.predicate} 从 {old_obj} 改为 {triplet.object}")
                        graph_db.delete_relation(triplet.subject, triplet.predicate, old_obj, agent_id)
                        self._do_graph_write(graph_db, triplet, event.source_item_id, agent_id)

                    elif action == UpdateAction.ARCHIVE:
                        print(f"🗄️ [Dispatcher] 知识存档: {old_obj} 变为过去式")
                        graph_db.delete_relation(triplet.subject, triplet.predicate, old_obj, agent_id)
                        graph_db.add_relation(triplet.subject, f"曾{triplet.predicate}", old_obj, event.source_item_id,
                                              agent_id)
                        self._do_graph_write(graph_db, triplet, event.source_item_id, agent_id)

                    elif action == UpdateAction.APPEND:
                        print(f"➕ [Dispatcher] 知识追加: {triplet.object}")
                        self._do_graph_write(graph_db, triplet, event.source_item_id, agent_id)

                    elif action == UpdateAction.IGNORE:
                        print(f"🙈 [Dispatcher] 知识忽略: {triplet.object}")


            except Exception as e:
                # 遇到图谱内部错误，同样把堆栈打出来
                import traceback
                print(f"⚠️ [Dispatcher] 图谱演化处理失败:")
                traceback.print_exc()

    # ✨ 修补 5：底层写入封装带上 agent_id
    def _do_graph_write(self, graph_db, triplet, source_id, agent_id):
        """原子写入操作"""
        graph_db.add_relation(
            sub=triplet.subject,
            pred=triplet.predicate,
            obj=triplet.object,
            memory_id=source_id,
            agent_id=agent_id
        )

    # ==========================================
    # 核心链路 3：处理清道夫压缩事件
    # ==========================================
    def _handle_consolidation(self, event: ConsolidationTriggerEvent):
        if self.consolidate_hub:
            self.consolidate_hub.handle_event(event)
        else:
            print("⚠️ [Dispatcher] 收到压缩事件，但系统未注册 ConsolidateHub，已跳过处理。")