# @Time    :2026/4/18 19:36
# @Author  :进喜
# @File    :working_cache.py
# @Software:PyCharm


"""实现"最近 N 条记忆滑动窗口"的最优雅方案并不是用普通的 List（列表），而是使用内置的 collections.deque（双端队列）。它的神仙之处在于：一旦设置了最大长度，当你塞入第 21 条记忆时，最老的第 1 条记忆会被系统瞬间自动挤出去，完全不需要你手动写代码去删除。"""
from collections import deque
from typing import List
from ..schema.memory_item import MemoryItem, MemoryStage


class WorkingMemoryCache:
    """
    L0 级工作记忆 (Working Memory)
    基于内存的极速缓存，专为"秒回"和"上下文连贯性"设计。
    特点：读写耗时几乎为 0，程序重启后自动销毁（持久化由 sqlite_log 兜底）。
    """

    def __init__(self, max_size: int = 20):
        # maxlen 是灵魂：超过这个数字，旧数据会自动被推出队列，永远不会内存溢出
        self.max_size = max_size
        self.cache: deque[MemoryItem] = deque(maxlen=self.max_size)

    def add(self, item: MemoryItem):
        """
        向工作记忆中添加一条新对话。
        """
        # 🛡️ 架构师的小细节：在这里强制校验或覆盖 stage，确保生命周期绝对正确
        item.stage = MemoryStage.WORKING  # 工作记忆
        self.cache.append(item)

    def get_all(self) -> List[MemoryItem]:
        """
        获取当前窗口内的所有上下文对话。
        (返回普通 List，方便组装进大模型的 Prompt)
        """
        return list(self.cache)

    def get_recent(self, limit: int) -> List[MemoryItem]:
        """
        仅获取最近的 limit 条记录（用于某些只需要极短上下文的快捷判断）。
        """
        return list(self.cache)[-limit:]

    def remove(self, memory_id: str):
        """Remove a single item from cache by its memory id."""
        for item in list(self.cache):
            if item.id == memory_id:
                self.cache.remove(item)
                return

    def clear(self):
        """
        清空工作记忆。
        场景：玩家跟张三说"我们换个话题吧"，或者在系统中触发了强行重置指令。
        """
        self.cache.clear()

    def __len__(self) -> int:
        """支持直接使用 len(cache) 查看当前有几条记忆"""
        return len(self.cache)