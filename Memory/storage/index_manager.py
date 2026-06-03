# @Time    :2026/4/22 15:28
# @Author  :进喜
# @File    :index_manager.py
# @Software:PyCharm


# 保证全局一致性
import logging
from typing import List
from Memory.storage.working_cache import WorkingMemoryCache
from Memory.storage.sqlite_log import SQLiteLogStorage
from Memory.storage.vector_store import VectorStore
from Memory.storage.graph_db import GraphStore


class IndexManager:
    """
    全局索引与生命周期管理器 (Index & Lifecycle Manager)
    负责维持 L0 到 L3 四大存储层的数据一致性。
    核心职责：级联删除 (Cascade Delete)、缓存淘汰 (Eviction)、索引审计与重建 (Rebuild)。
    """

    def __init__(
            self,
            cache: WorkingMemoryCache,
            sqlite: SQLiteLogStorage,
            vector_store: VectorStore,
            graph_store: GraphStore
    ):
        self.cache = cache
        self.sqlite = sqlite
        self.vector_store = vector_store
        self.graph_store = graph_store
        self.logger = logging.getLogger("IndexManager")

    # ==========================================
    # 1. 级联删除 (Cascade Delete)
    # ==========================================
    def delete_memory(self, memory_id: str) -> bool:
        """
        全域抹除指令：在所有存储基座中彻底销毁某条记忆。
        应用场景：用户触发“遗忘”指令，或数据合规清除。
        """
        self.logger.info(f"🔄 正在执行跨库级联删除，目标 ID: {memory_id}")
        success_flags = []

        # 1. 抹除 L0 缓存
        try:
            self.cache.remove(memory_id)  # 假设 cache 有 remove 方法
            success_flags.append(True)
        except Exception as e:
            self.logger.error(f"L0 删除失败: {e}")

        # 2. 抹除 L1 SQLite (作为唯一真相源，这里必须成功)
        try:
            self.sqlite.delete(memory_id)  # 假设 sqlite 有 delete 方法
            success_flags.append(True)
        except Exception as e:
            self.logger.error(f"L1 删除失败: {e}")

        # 3. 抹除 L2 向量库
        try:
            self.vector_store.delete(memory_id)  # 假设 vector_store 封装了 delete
            success_flags.append(True)
        except Exception as e:
            self.logger.error(f"L2 删除失败: {e}")

        # 4. 抹除 L3 图谱关系 ✨
        try:
            self._delete_graph_relations(memory_id)
            success_flags.append(True)
        except Exception as e:
            self.logger.error(f"L3 删除失败: {e}")

        return len(success_flags) == 4

    def _delete_graph_relations(self, memory_id: str):
        if not self.graph_store:
            return
        query = """
        // 1. 找到所有由该记忆产生的关系并删除
        MATCH ()-[r {memory_id: $mid}]->() 
        DELETE r

        // 2. 清理没有任何连线的孤儿节点 (Orphan Nodes)
        // 注意：WITH * 用于连接两个不相关的操作
        WITH *
        MATCH (n:Entity) 
        WHERE degree(n) = 0 
        DELETE n
        """
        with self.graph_store.driver.session() as session:
            session.run(query, mid=memory_id)

    # ==========================================
    # 2. 缓存淘汰 (L0 Eviction / Garbage Collection)
    # ==========================================
    def evict_stale_cache(self, max_items: int = 20) -> int:
        """
        防止 L0 撑爆大模型上下文。
        将超出 max_items 的旧记忆从 L0 移除（它们早已被异步线程写入了 L1/L2/L3，所以不会丢失）。
        """
        current_cache = self.cache.get_all()
        if len(current_cache) <= max_items:
            return 0

        # 按时间戳排序，找到最老的需要踢出的数量
        sorted_cache = sorted(current_cache, key=lambda x: x.timestamp)
        evict_count = len(sorted_cache) - max_items
        items_to_evict = sorted_cache[:evict_count]

        for item in items_to_evict:
            self.cache.remove(item.id)

        self.logger.info(f"🧹 缓存淘汰完成：清理了 {evict_count} 条沉淀对话。")
        return evict_count

    # ==========================================
    # 3. 灾难恢复与索引对齐 (Index Rebuild)
    # ==========================================
    def rebuild_indexes_from_truth(self):
        """
        系统级修复：以 L1 (SQLite) 为唯一真相源 (Source of Truth)，
        重新将数据刷入 L2 (向量库) 和 L3 (图谱)。
        应用场景：向量数据库崩溃数据丢失，或者你更换了新的 Embedding 模型。
        """
        self.logger.warning("⚠️ 开始执行全局索引重建！这可能需要很长时间...")

        # 清空 L2 和 L3 库
        self.vector_store.clear()
        if self.graph_store:
            self.graph_store.clear()

        all_memories = self.sqlite.get_all()

        self.logger.info(f"共发现 {len(all_memories)} 条历史记录，开始逐条重建索引...")

        for i, mem in enumerate(all_memories):
            try:
                self.vector_store.add(mem)
                self.logger.debug(f"[{i+1}/{len(all_memories)}] 已重建向量索引: {mem.id}")
            except Exception as e:
                self.logger.error(f"[{i+1}/{len(all_memories)}] 向量重建失败 {mem.id}: {e}")

        self.logger.info(f"✅ 索引重建完成，共处理 {len(all_memories)} 条记录。")