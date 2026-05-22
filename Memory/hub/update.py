# @Time    :2026/5/6 13:21
# @Author  :进喜
# @File    :update.py
# @Software:PyCharm


'''
L1 SQLite 是唯一真相源 (Source of Truth)。
向量 (L2) 和图谱 (L3) 是基于文本的衍生品。文本一旦修改，向量必须重算，三元组必须重抽。
不能破坏现有的 Schema 契约 (MemoryItem, BackendTarget 等)。

直接复用刚才写的 IndexManager (全域删除) 和 AsyncDispatcher (全域写入) 来完成这个 UpdateHub
'''

from typing import Optional, Dict, Any
from ..schema.memory_item import MemoryItem
from ..schema.routing import BackendTarget
from ..storage.sqlite_log import SQLiteLogStorage
from ..storage.index_manager import IndexManager
from ..hub.async_dispatcher import AsyncDispatcher


class UpdateHub:
    """
    记忆更新与纠偏中枢 (Update Hub)
    负责对已存在的记忆进行修改（如：事实纠正、内容追加、元数据更新）。
    采用 Wipe and Replace (抹除并重置) 策略，确保四大存储层的索引绝对一致。
    """

    def __init__(
            self,
            sqlite: SQLiteLogStorage,
            index_manager: IndexManager,
            dispatcher: AsyncDispatcher
    ):
        self.sqlite = sqlite
        self.index_manager = index_manager
        self.dispatcher = dispatcher

    def update_content(self, memory_id: str, new_content: str) -> bool:
        """
        更新记忆的核心内容 (Content)。
        由于内容改变，L2 的向量 Embedding 和 L3 的图谱三元组将全部失效，必须重新生成。
        """
        # 1. 从唯一真相源 (SQLite) 捞出原始记忆对象
        # 假设 SQLiteLogStorage 提供了一个根据 ID 获取单条记录的方法 get(id)
        original_item: Optional[MemoryItem] = self.sqlite.get(memory_id)

        if not original_item:
            print(f"⚠️ [UpdateHub] 未找到指定记忆，更新失败。ID: {memory_id}")
            return False

        # 2. 全域抹除旧数据
        # 这一步会把 L0, L1, L2, L3 中与该 memory_id 相关的文本、向量、图谱连线彻底删除
        delete_success = self.index_manager.delete_memory(memory_id)
        if not delete_success:
            print(f"⚠️ [UpdateHub] 旧数据级联删除出现异常，为保证一致性，终止更新。")
            return False

        # 3. 基于原有契约更新内容
        # 使用 pydantic 的特性，保留原有 id, role, timestamp 等关键属性，只替换 content
        updated_item = original_item.model_copy(update={"content": new_content})

        # 4. 将更新后的记忆重新打入异步车间，进行全域重建
        # 这会让系统重新为其计算向量、抽取最新的图谱关系并落盘
        self.dispatcher.submit_write_job(
            item=updated_item,
            targets=[BackendTarget.SQLITE, BackendTarget.VECTOR, BackendTarget.GRAPH]
        )

        print(f"✅ [UpdateHub] 记忆更新已投递至异步队列。ID: {memory_id}")
        return True

    def update_metadata(self, memory_id: str, new_metadata_kvs: Dict[str, Any]) -> bool:
        """
        仅更新记忆的元数据 (Metadata)。
        例如：给某条记忆打上 {"is_important": True} 的标签。
        这种更新通常不需要重新计算向量和图谱，只需更新 SQLite，(如果有需要)再同步给特定的库。
        """
        original_item: Optional[MemoryItem] = self.sqlite.get(memory_id)

        if not original_item:
            return False

        # 合并现有的 metadata 和新传入的键值对
        current_metadata = original_item.metadata or {}
        current_metadata.update(new_metadata_kvs)

        updated_item = original_item.model_copy(update={"metadata": current_metadata})

        # 由于仅仅是 metadata 变动，我们不需要全域 Wipe。
        # 1. 覆盖写入 SQLite (假设 sqlite 内部 add 支持基于 ID 的 UPSERT / 覆盖)
        self.sqlite.add(updated_item)

        # 2. 覆盖写入 VectorStore (假设 vector_store 的 add 也支持基于 ID 覆盖元数据)
        self.dispatcher.backends[BackendTarget.VECTOR].add(updated_item)

        print(f"✅ [UpdateHub] 元数据更新完成。ID: {memory_id}")
        return True
