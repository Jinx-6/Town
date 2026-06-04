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
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row  # 按列名访问，消除固定索引脆弱性
        return conn

    @staticmethod
    def _safe_json(value, default):
        """安全 JSON 解析：None/空串/无效 JSON 均返回默认值"""
        if not value:
            return default
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError) as e:
            print(f"⚠️ [SQLiteLog] JSON 解析失败，使用默认值: {e}")
            return default

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

    def get_all(self) -> List[MemoryItem]:
        """获取表中全部记忆记录"""
        query = "SELECT * FROM episodic_memory ORDER BY timestamp DESC"
        with self._get_connection() as conn:
            cursor = conn.execute(query)
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

    def search_with_metadata(self, query: str, agent_id: str = None, limit: int = 5,
                             metadata_filters: Optional[Dict[str, Any]] = None) -> List[MemoryItem]:
        """关键词搜索 + metadata 字段硬性过滤（json_extract）。"""
        words = [w for w in query.split() if len(w) >= 1]
        if not words:
            return []

        clauses = ["(" + " OR ".join(["content LIKE ?"] * len(words)) + ")"]
        params = [f"%{w}%" for w in words]

        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)

        if metadata_filters:
            for key, value in metadata_filters.items():
                if isinstance(value, bool):
                    clauses.append(f"json_extract(metadata_json, '$.{key}') = ?")
                    params.append(1 if value else 0)
                elif isinstance(value, str):
                    clauses.append(f"json_extract(metadata_json, '$.{key}') = ?")
                    params.append(value)
                elif isinstance(value, int):
                    clauses.append(f"json_extract(metadata_json, '$.{key}') = ?")
                    params.append(value)

        sql = "SELECT * FROM episodic_memory WHERE " + " AND ".join(clauses)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with self._get_connection() as conn:
            cursor = conn.execute(sql, params)
            return [self._row_to_item(row) for row in cursor.fetchall()]

    def _row_to_item(self, row) -> MemoryItem:
        """将数据库行转为 MemoryItem，使用列名访问，安全 JSON 解析"""
        return MemoryItem(
            id=row["id"],
            content=row["content"],
            role=MemoryRole(row["role"]),
            timestamp=datetime.fromisoformat(row["timestamp"]),
            stage=MemoryStage(row["stage"]),
            agent_id=row["agent_id"] if "agent_id" in row.keys() and row["agent_id"] else "default_agent",
            metadata=self._safe_json(row["metadata_json"], {}),
            vector=self._safe_json(row["vector_json"], None)
        )