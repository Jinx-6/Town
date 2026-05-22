# @Time    :2026/4/18 14:57
# @Author  :进喜
# @File    :sqlite_log.py
# @Software:PyCharm

import sqlite3
import json
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from ..schema.memory_item import MemoryItem, MemoryRole, MemoryStage

class SQLiteLogStorage:
    def __init__(self, db_path: str = "cyber_town.db"):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self):
        # 允许在多线程环境下使用（结合我们的 AsyncDispatcher 极其重要）
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def _init_db(self):
        """初始化表结构，如果不存在则创建"""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS episodic_memory (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    role TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    agent_id TEXT DEFAULT 'default_agent',
                    metadata_json TEXT,
                    vector_json TEXT
                )
            """)
            # 向前兼容：旧表可能缺少 agent_id 列
            try:
                conn.execute("ALTER TABLE episodic_memory ADD COLUMN agent_id TEXT DEFAULT 'default_agent'")
            except Exception:
                pass  # 列已存在则忽略
            conn.commit()

    def add(self, item: MemoryItem):
        """新增或覆盖一条记忆 (支持 UPSERT)"""
        role_str = getattr(item.role, 'value', item.role)
        stage_str = getattr(item.stage, 'value', item.stage)

        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO episodic_memory (id, content, role, timestamp, stage, agent_id, metadata_json, vector_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item.id,
                    item.content,
                    role_str,
                    item.timestamp.isoformat(),
                    stage_str,
                    item.agent_id,
                    json.dumps(item.metadata.model_dump()),
                    json.dumps(item.vector) if item.vector else None
                )
            )

    def get_by_id(self, memory_id: str) -> Optional[MemoryItem]:
        """根据 ID 获取单条记忆"""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM episodic_memory WHERE id = ?", (memory_id,))
            row = cursor.fetchone()
            return self._row_to_item(row) if row else None

    # ✨ 改动 2：增加别名，兼容我们 Hub 层 self.sqlite.get() 的调用习惯
    get = get_by_id

    def list_recent(self, limit: int = 10, role: Optional[MemoryRole] = None) -> List[MemoryItem]:
        """获取最近的记忆列表"""
        query = "SELECT * FROM episodic_memory"
        params = []
        if role:
            query += " WHERE role = ?"
            # ✨ 加固：安全获取查询条件的枚举值
            role_str = getattr(role, 'value', role)
            params.append(role_str)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with self._get_connection() as conn:
            cursor = conn.execute(query, tuple(params))
            return [self._row_to_item(row) for row in cursor.fetchall()]

    def update(self, item: MemoryItem):
        """更新已有记忆（用于反思、重要度重排等场景）"""
        # ✨ 加固：安全获取 stage 的值
        stage_str = getattr(item.stage, 'value', item.stage)

        with self._get_connection() as conn:
            conn.execute(
                "UPDATE episodic_memory SET content=?, stage=?, metadata_json=? WHERE id=?",
                (item.content, stage_str, json.dumps(item.metadata.model_dump()), item.id)
            )

    def delete(self, memory_id: str):
        """物理删除"""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM episodic_memory WHERE id = ?", (memory_id,))

    # ✨ 改动 3：专门为 ConsolidateHub 提供的冷数据打捞接口
    def get_oldest_episodic_memories(self, limit: int = 10, days_before: int = 7) -> List[MemoryItem]:
        """
        获取 N 天前的最老情境记忆，用于记忆压缩。
        只检索 stage 为 EPISODIC (流水账) 的数据，跳过已被压缩的语义数据。
        """
        threshold_date = datetime.now() - timedelta(days=days_before)
        threshold_iso = threshold_date.isoformat()

        query = """
            SELECT * FROM episodic_memory 
            WHERE stage = ? AND timestamp <= ? 
            ORDER BY timestamp ASC 
            LIMIT ?
        """
        params = (MemoryStage.EPISODIC.value, threshold_iso, limit)

        with self._get_connection() as conn:
            cursor = conn.execute(query, params)
            return [self._row_to_item(row) for row in cursor.fetchall()]

    def search_text(self, query: str, agent_id: str = None, limit: int = 5) -> List[MemoryItem]:
        """基于关键词分词 OR 匹配的文本搜索，支持 agent_id 隔离"""
        words = [w for w in query.split() if len(w) >= 1]
        if not words:
            return []

        clauses = " OR ".join(["content LIKE ?"] * len(words))
        sql = f"SELECT * FROM episodic_memory WHERE ({clauses})"
        params = [f"%{w}%" for w in words]

        if agent_id:
            sql += " AND agent_id = ?"
            params.append(agent_id)

        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with self._get_connection() as conn:
            cursor = conn.execute(sql, params)
            return [self._row_to_item(row) for row in cursor.fetchall()]

    def _row_to_item(self, row: tuple) -> MemoryItem:
        """内部辅助：将数据库行转换为 MemoryItem 对象
        列顺序: id, content, role, timestamp, stage, agent_id, metadata_json, vector_json
        """
        return MemoryItem(
            id=row[0],
            content=row[1],
            role=MemoryRole(row[2]),
            timestamp=datetime.fromisoformat(row[3]),
            stage=MemoryStage(row[4]),
            agent_id=row[5] if len(row) > 5 and row[5] else "default_agent",
            metadata=json.loads(row[6]) if len(row) > 6 and row[6] else {},
            vector=json.loads(row[7]) if len(row) > 7 and row[7] else None
        )