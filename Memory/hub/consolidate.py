# @Time    :2026/5/12 16:30
# @Author  :进喜
# @File    :consolidate.py

import uuid
import time
import threading
from typing import List, Optional
from datetime import datetime, timezone

from ..schema.memory_item import MemoryItem, MemoryRole, MemoryStage
from ..schema.routing import BackendTarget
from ..schema.events import ConsolidationTriggerEvent, MemoryWriteEvent
from ..storage.sqlite_log import SQLiteLogStorage
from ..storage.index_manager import IndexManager
from ..hub.async_dispatcher import AsyncDispatcher
from ..processor.summarizer.long_term import LongTermSummarizer
# ✨ 引入遗忘策略
from ..policies.retention import RetentionPolicy


class ConsolidateHub:
    """
    记忆压缩与新陈代谢中枢 (Consolidate Hub)
    【策略升级版】：接入 RetentionPolicy，执行“分级压缩”与“垃圾回收(GC)”。
    """

    def __init__(
            self,
            sqlite: SQLiteLogStorage,
            index_manager: IndexManager,
            dispatcher: AsyncDispatcher,
            summarizer: LongTermSummarizer,
            retention_policy: RetentionPolicy  # ✨ 注入遗忘策略裁判官
    ):
        self.sqlite = sqlite
        self.index_manager = index_manager
        self.dispatcher = dispatcher
        self.summarizer = summarizer
        self.retention_policy = retention_policy

        self._running = False

    # ==========================================
    # 自律生物钟 (触发源)
    # ==========================================
    def start_auto_compress_cron(self, interval_hours: int = 12):
        if self._running:
            return

        self._running = True
        cron_thread = threading.Thread(
            target=self._cron_loop,
            args=(interval_hours,),
            daemon=True
        )
        cron_thread.start()
        print(f"⏰ [ConsolidateHub] 生物钟启动，每 {interval_hours} 小时执行一次新陈代谢。")

    def _cron_loop(self, interval_hours: int):
        while self._running:
            time.sleep(interval_hours * 3600)

            print("⏰ [ConsolidateHub] 生物钟到点，开始发布新陈代谢事件...")
            # ✨ 不再硬编码 days_before，一切交由 Policy 裁决
            trigger_event = ConsolidationTriggerEvent(
                trigger_reason="scheduled_cron",
                batch_size=50
            )
            self.dispatcher.publish(trigger_event)

    # ==========================================
    # 核心干活逻辑 (响应总线分发)
    # ==========================================
    def handle_event(self, event: ConsolidationTriggerEvent):
        """统一的事件处理入口"""
        print(f"🧹 [ConsolidateHub] 收到新陈代谢指令，原因: {event.trigger_reason}")

        # 为了不阻塞主线程或数据库，我们可以先拉取一批候选数据
        # 假设 sqlite 有一个方法可以拉取按照时间倒序的 N 条数据
        candidates = self.sqlite.list_recent(limit=event.batch_size * 2)

        if not candidates:
            return

        # 1. 执行垃圾回收 (物理销毁彻底过期的数据)
        self._run_garbage_collection(candidates)

        # 2. 执行记忆压缩 (提纯成熟的流水账)
        self._run_compression(candidates)

    def _run_garbage_collection(self, candidates: List[MemoryItem]):
        """✨ 垃圾回收机制：清理彻底过期的记忆"""
        deleted_count = 0
        now = datetime.now(timezone.utc)

        for item in candidates:
            # 询问裁判官：这根草该拔了吗？
            if self.retention_policy.should_delete(item, now):
                # 跨多库全域抹除
                self.index_manager.delete_memory(item.id)
                deleted_count += 1

        if deleted_count > 0:
            print(f"🗑️ [ConsolidateHub] 垃圾回收完毕：彻底删除了 {deleted_count} 条过期记忆。")

    def _run_compression(self, candidates: List[MemoryItem]):
        """✨ 记忆提纯机制：把成熟的流水账总结为高维记忆"""
        now = datetime.now(timezone.utc)
        to_compress = []

        for item in candidates:
            # 询问裁判官：这批流水账成熟了吗？
            if self.retention_policy.should_compress(item, now):
                to_compress.append(item)

        if len(to_compress) < 2:
            return  # 条数太少，不够总结的

        # 文本拼接
        dialogue_text = ""
        for item in to_compress:
            speaker = "用户" if item.role == MemoryRole.USER else "张三"
            dialogue_text += f"{speaker}：{item.content}\n"

        # 调用 AI 处理器进行语义提纯
        summary = self.summarizer.compress_to_semantic(dialogue_text)

        if not summary:
            print("⚠️ [ConsolidateHub] AI 提纯失败或返回空，跳过压缩。")
            return

        # 创建高维压缩记忆 (Stage: SEMANTIC)
        consolidated_item = MemoryItem(
            id=f"sum_{uuid.uuid4().hex[:8]}",
            content=f"【阶段总结】{summary}",
            role=MemoryRole.SYSTEM,
            stage=MemoryStage.SEMANTIC,
            metadata={
                "type": "consolidated_summary",
                "compressed_count": len(to_compress),
                "source_ids": [item.id for item in to_compress]
            }
        )

        # 抹除旧的琐碎数据（已经被总结，没有保留价值了）
        for item in to_compress:
            self.index_manager.delete_memory(item.id)

        # 注入新记忆，发布写入事件
        write_event = MemoryWriteEvent(
            item=consolidated_item,
            targets=[BackendTarget.SQLITE, BackendTarget.VECTOR]
        )
        self.dispatcher.publish(write_event)

        print(f"✅ [ConsolidateHub] 记忆提炼完成，浓缩了 {len(to_compress)} 条对话。新记忆 ID: {consolidated_item.id}")