import os
import time
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

from datetime import datetime, timedelta, timezone
from Memory.schema.memory_item import MemoryItem, MemoryRole, MemoryStage
from Memory.storage.sqlite_log import SQLiteLogStorage


class TestSQLiteStorage:
    """L1 情景记忆 (SQLite) 隔离测试"""

    def setup_method(self):
        self.db_path = "test_cyber_town_temp.db"
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        self.storage = SQLiteLogStorage(db_path=self.db_path)

    def teardown_method(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_write_and_readback(self):
        """数据写入与完美回读 (序列化闭环)"""
        item = MemoryItem(
            content="玩家发现了一个带血的扳手。",
            role=MemoryRole.USER,
            stage=MemoryStage.SENSORY
        )
        item.metadata.importance = 9
        item.metadata.entities = ["扳手", "血迹"]

        self.storage.add(item)
        retrieved = self.storage.get(item.id)

        assert retrieved is not None, "写入失败，无法查到数据"
        assert retrieved.content == item.content, "文本内容损坏"
        assert retrieved.metadata.importance == 9, "嵌套的 metadata 重要度丢失"
        assert "扳手" in retrieved.metadata.entities, "嵌套的 metadata 实体丢失"

    def test_update_upsert(self):
        """Update 覆盖更新机制 (UPSERT)"""
        item = MemoryItem(
            content="玩家发现了一个带血的扳手。",
            role=MemoryRole.USER,
            stage=MemoryStage.SENSORY
        )
        self.storage.add(item)

        retrieved = self.storage.get(item.id)
        retrieved.content = "玩家拿起了带血的扳手并放进背包。"
        retrieved.stage = MemoryStage.WORKING
        self.storage.update(retrieved)

        updated = self.storage.get(retrieved.id)
        assert updated.content == "玩家拿起了带血的扳手并放进背包。", "Update 更新内容失败"
        assert updated.stage == MemoryStage.WORKING.value, "Update 更新阶段失败"

    def test_list_recent_order(self):
        """多条记录查询与时序排序"""
        item2 = MemoryItem(content="系统提示：背包已满", role=MemoryRole.SYSTEM)
        time.sleep(0.1)
        item3 = MemoryItem(content="NPC说：你好啊冒险者", role=MemoryRole.ASSISTANT)

        self.storage.add(item2)
        self.storage.add(item3)

        recent = self.storage.list_recent(limit=2)
        assert len(recent) == 2, "Limit 限制失败"
        assert recent[0].id == item3.id, "时间倒序排序失败"

    def test_cold_data_retrieval(self):
        """冷数据打捞 (时间窗口查询)"""
        old_item = MemoryItem(content="十天前的事情", role=MemoryRole.USER, stage=MemoryStage.EPISODIC)
        old_item.timestamp = datetime.now(timezone.utc) - timedelta(days=10)
        self.storage.add(old_item)

        new_item = MemoryItem(content="前天的事情", role=MemoryRole.USER, stage=MemoryStage.EPISODIC)
        new_item.timestamp = datetime.now(timezone.utc) - timedelta(days=2)
        self.storage.add(new_item)

        old_memories = self.storage.get_oldest_episodic_memories(limit=5, days_before=7)
        assert len(old_memories) == 1, f"捞出了错误数量的数据：{len(old_memories)}条"
        assert old_memories[0].id == old_item.id, "捞出了错误的时间数据"

    def test_hard_delete(self):
        """物理删除"""
        item = MemoryItem(content="待删除的记录", role=MemoryRole.SYSTEM)
        self.storage.add(item)
        assert self.storage.get(item.id) is not None

        self.storage.delete(item.id)
        assert self.storage.get(item.id) is None, "数据删除失败"
