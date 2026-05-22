# test_sqlite_log.py
import os
import time
from datetime import datetime, timedelta, timezone

# 替换为你实际的绝对路径导入
from Town.Memory.schema.memory_item import MemoryItem, MemoryRole, MemoryStage
from Town.Memory.storage.sqlite_log import SQLiteLogStorage


def run_storage_tests():
    print("🚀 启动 L1 情景记忆 (SQLite) 隔离测试...")
    db_path = "test_cyber_town.db"

    # 清理历史测试残留
    if os.path.exists(db_path):
        os.remove(db_path)

    storage = SQLiteLogStorage(db_path=db_path)

    try:
        # ==========================================
        # 测试 1: 基础写入与读取 (序列化闭环测试)
        # ==========================================
        print("\n[Test 1] 测试数据写入与完美回读...")
        item1 = MemoryItem(
            content="玩家发现了一个带血的扳手。",
            role=MemoryRole.USER,
            stage=MemoryStage.SENSORY
        )
        # 强行给 metadata 赋值，测试深层序列化
        item1.metadata.importance = 9
        item1.metadata.entities = ["扳手", "血迹"]

        storage.add(item1)
        retrieved_item = storage.get(item1.id)

        assert retrieved_item is not None, "写入失败，无法查到数据"
        assert retrieved_item.content == item1.content, "文本内容损坏"
        assert retrieved_item.metadata.importance == 9, "嵌套的 metadata 重要度丢失！"
        assert "扳手" in retrieved_item.metadata.entities, "嵌套的 metadata 实体丢失！"
        print("✅ [Test 1] 写入与序列化闭环测试通过！")

        # ==========================================
        # 测试 2: Update 覆盖更新机制 (UPSERT)
        # ==========================================
        print("\n[Test 2] 测试数据更新机制...")
        retrieved_item.content = "玩家拿起了带血的扳手并放进背包。"
        retrieved_item.stage = MemoryStage.WORKING
        storage.update(retrieved_item)

        updated_item = storage.get(retrieved_item.id)
        assert updated_item.content == "玩家拿起了带血的扳手并放进背包。", "Update 更新内容失败"
        assert updated_item.stage == MemoryStage.WORKING.value, "Update 更新阶段失败"
        print("✅ [Test 2] 数据更新与状态流转测试通过！")

        # ==========================================
        # 测试 3: List 查询与排序
        # ==========================================
        print("\n[Test 3] 测试多条记录查询与时序排序...")
        item2 = MemoryItem(content="系统提示：背包已满", role=MemoryRole.SYSTEM)
        time.sleep(0.1)  # 稍微暂停，制造时间差
        item3 = MemoryItem(content="NPC说：你好啊冒险者", role=MemoryRole.ASSISTANT)

        storage.add(item2)
        storage.add(item3)

        recent_items = storage.list_recent(limit=2)
        assert len(recent_items) == 2, "Limit 限制失败"
        # 验证倒序：最近存入的 item3 应该排在第一个
        assert recent_items[0].id == item3.id, "时间倒序排序失败"
        print("✅ [Test 3] 时序查询列表测试通过！")

        # ==========================================
        # 测试 4: 冷数据打捞逻辑 (Consolidate 专用)
        # ==========================================
        print("\n[Test 4] 测试冷数据打捞 (复杂时间条件)...")
        # 伪造一条 10 天前的记忆
        old_item = MemoryItem(content="十天前的事情", role=MemoryRole.USER, stage=MemoryStage.EPISODIC)
        old_item.timestamp = datetime.now(timezone.utc) - timedelta(days=10)
        storage.add(old_item)

        # 伪造一条 2 天前的记忆
        new_item = MemoryItem(content="前天的事情", role=MemoryRole.USER, stage=MemoryStage.EPISODIC)
        new_item.timestamp = datetime.now(timezone.utc) - timedelta(days=2)
        storage.add(new_item)

        # 打捞 7 天前的数据，应该只能捞出 old_item
        old_memories = storage.get_oldest_episodic_memories(limit=5, days_before=7)
        assert len(old_memories) == 1, f"捞出了错误数量的数据：{len(old_memories)}条"
        assert old_memories[0].id == old_item.id, "捞出了错误的时间数据"
        print("✅ [Test 4] 时间窗口数据打捞测试通过！")

        # ==========================================
        # 测试 5: 物理删除
        # ==========================================
        print("\n[Test 5] 测试数据硬删除...")
        storage.delete(item2.id)
        assert storage.get(item2.id) is None, "数据删除失败"
        print("✅ [Test 5] 数据删除测试通过！")

        print("\n🎉 恭喜！你的 SQLite 物理层全部测试通过，存储引擎运转完美！")

    except Exception as e:
        print(f"\n❌ 测试失败！")
        import traceback
        traceback.print_exc()
    finally:
        # 测完清理测试库（可选，如果你想用 SQLite 客户端打开看看，可以注释掉下面两行）
        if os.path.exists(db_path):
            os.remove(db_path)


if __name__ == "__main__":
    run_storage_tests()