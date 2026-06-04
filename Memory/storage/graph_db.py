# @Time    :2026/4/22 15:06
# @Author  :进喜
# @File    :graph_db.py
# @Software:PyCharm


from neo4j import GraphDatabase
from typing import List, Dict, Any, Optional
from ..schema.memory_item import MemoryItem, MemoryRole, MemoryStage
from datetime import datetime

class GraphStore:
    """
    L3 级图谱记忆 (Graph Memory) - 身份隔离版
    实体(Entity)共享，关系(RELATION)私有。通过 agent_id 确保居民间的认知互不干扰。
    """
    def __init__(self, uri: str = "bolt://localhost:7687", user: str = "neo4j", password: str = "password"):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self):
        self.driver.close()

    # ==========================================
    #  核心写入与演化操作 (增加 agent_id)
    # ==========================================
    def add_relation(self, sub: str, pred: str, obj: str, memory_id: str, agent_id: str):
        """
        ✨ 改造：为关系边添加所属 Agent ID
        """
        with self.driver.session() as session:
            session.execute_write(self._create_relation_tx, sub, pred, obj, memory_id, agent_id)

    def delete_relation(self, sub: str, pred: str, obj: str, agent_id: str):
        """
        ✨ 改造：仅删除属于该 Agent 的特定关系
        """
        with self.driver.session() as session:
            session.execute_write(self._delete_relation_tx, sub, pred, obj, agent_id)

    def clear(self):
        """清空图谱全部数据（用于索引重建）"""
        with self.driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")

    def get_objects_for_predicate(self, sub: str, pred: str, agent_id: str) -> List[str]:
        """
        ✨ 改造：查询特定 Agent 认知下的事实
        """
        with self.driver.session() as session:
            return session.execute_read(self._get_objects_tx, sub, pred, agent_id)

    # ==========================================
    #  核心检索操作 (增加 agent_id 过滤)
    # ==========================================
    def search_subgraph(self, entity_name: str, agent_id: str, depth: int = 2) -> List[Dict[str, Any]]:
        """
        ✨ 改造：打捞属于该 Agent 的私有知识子图
        """
        with self.driver.session() as session:
            return session.execute_read(self._query_subgraph_tx, entity_name, agent_id, depth)

    def get_memories_by_entity(self, entity_name: str, agent_id: str) -> List[str]:
        """
        ✨ 改造：查找属于该 Agent 且提到该实体的记忆 ID
        """
        with self.driver.session() as session:
            query = (
                "MATCH (e:Entity {name: $name})<-[r:RELATION {agent_id: $agent_id}]-() "
                "RETURN DISTINCT r.memory_id as mid"
            )
            result = session.run(query, name=entity_name, agent_id=agent_id)
            return [record["mid"] for record in result]

    # ==========================================
    #  Cypher 原子事务封装区 (核心逻辑)
    # ==========================================
    @staticmethod
    def _create_relation_tx(tx, sub, pred, obj, memory_id, agent_id):
        # 实体用 MERGE (全图唯一)，关系用 MERGE 结合 agent_id (确保每个 Agent 的边独立)
        query = (
            "MERGE (s:Entity {name: $sub}) "
            "MERGE (o:Entity {name: $obj}) "
            "MERGE (s)-[r:RELATION {type: $pred, agent_id: $agent_id}]->(o) "
            "SET r.memory_id = $memory_id, r.updated_at = timestamp() "
            "RETURN s, r, o"
        )
        tx.run(query, sub=sub, pred=pred, obj=obj, memory_id=memory_id, agent_id=agent_id)

    @staticmethod
    def _delete_relation_tx(tx, sub, pred, obj, agent_id):
        query = (
            "MATCH (s:Entity {name: $sub})-[r:RELATION {type: $pred, agent_id: $agent_id}]->(o:Entity {name: $obj}) "
            "DELETE r"
        )
        tx.run(query, sub=sub, pred=pred, obj=obj, agent_id=agent_id)

    @staticmethod
    def _get_objects_tx(tx, sub, pred, agent_id):
        query = (
            "MATCH (s:Entity {name: $sub})-[r:RELATION {type: $pred, agent_id: $agent_id}]->(o:Entity) "
            "RETURN o.name as obj_name"
        )
        result = tx.run(query, sub=sub, pred=pred, agent_id=agent_id)
        return [record["obj_name"] for record in result]

    @staticmethod
    def _query_subgraph_tx(tx, entity_name, agent_id, depth):
        """
        深度打捞：确保路径上的每一跳关系都属于同一个 Agent
        """
        # 使用 ALL() 确保多跳路径中的每一个 rel 都满足 agent_id 条件
        query = (
            "MATCH p=(e:Entity {name: $name})-[r:RELATION*1..%d]-(neighbor) "
            "WHERE ALL(rel IN r WHERE rel.agent_id = $agent_id) "
            "RETURN e.name as start, neighbor.name as end, [rel in r | rel.type] as rels"
        ) % depth
        result = tx.run(query, name=entity_name, agent_id=agent_id)
        return [record.data() for record in result]