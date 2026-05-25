"""
Phase 1 — 多 Agent 记忆隔离测试
=================================
全部使用 MockVectorStore + 临时 SQLite，无外部依赖。

验证：
  - agent_id 写入隔离：A 的记忆 B 检索不到
  - SQLite / VectorStore 两层隔离
  - 不同 memory_type 不跨 agent 泄露
  - 重置单 agent 不影响其他 agent
"""
import sys, os, time, tempfile, shutil
from pathlib import Path
from typing import Optional, Dict, Any
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from Memory.schema.memory_item import MemoryItem, MemoryRole, MemoryStage, MemoryMetadata
from Memory.schema.routing import BackendTarget
from Memory.schema.retrieval import RetrievalRequest, RetrievedMemory, RetrievalResponse
from Memory.processor.planner import RetrievalPlan, RetrievalInstruction
from Memory.processor.assembler import PromptAssembler
from Memory.processor.router import WriteRouter

from Memory.storage.working_cache import WorkingMemoryCache
from Memory.storage.sqlite_log import SQLiteLogStorage

from Memory.policies.scoring import MemoryScorer
from Memory.policies.retrieval_policy import RetrievalPolicy
from Memory.policies.update_policy import UpdatePolicy

from Memory.hub.async_dispatcher import AsyncDispatcher
from Memory.hub.ingest import IngestHub
from Memory.hub.retrieve import RetrieveHub


# ============================================================
# Mock 向量库
# ============================================================
class MockVectorStore:
    def __init__(self):
        self.items: dict[str, MemoryItem] = {}

    def add(self, item: MemoryItem):
        self.items[item.id] = item

    def search(self, query: str, agent_id: str, limit: int = 5,
               metadata_filters: Optional[Dict[str, Any]] = None) -> list[RetrievedMemory]:
        results = []
        for item in self.items.values():
            if item.agent_id == agent_id:
                results.append(RetrievedMemory(
                    item=item, score=0.85, source=BackendTarget.VECTOR
                ))
        return results[:limit]


# ============================================================
# 共享 Fixture
# ============================================================
class IsolationTestBase:
    """隔离测试基类：两个 agent 共用一套存储后端"""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test_iso.db")

        self.cache = WorkingMemoryCache()
        self.sqlite = SQLiteLogStorage(db_path=self.db_path)
        self.mock_vector = MockVectorStore()

        self.router = WriteRouter()
        self.update_policy = UpdatePolicy()
        self.scorer = MemoryScorer()
        self.retrieval_policy = RetrievalPolicy()

        backends = {
            BackendTarget.SQLITE: self.sqlite,
            BackendTarget.VECTOR: self.mock_vector,
        }
        self.dispatcher = AsyncDispatcher(
            backends=backends, extractor=None, conflict_resolver=None,
            index_manager=None, update_policy=self.update_policy,
        )
        self.ingest = IngestHub(
            working_cache=self.cache, router=self.router, dispatcher=self.dispatcher
        )
        self.planner = MagicMock()
        self.retrieve = RetrieveHub(
            planner=self.planner, cache=self.cache, sqlite=self.sqlite,
            vector_store=self.mock_vector, graph_store=None,
            scorer=self.scorer, policy=self.retrieval_policy,
        )
        self.assembler = PromptAssembler(agent_name="测试")

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write(self, content, agent_id, role=MemoryRole.USER):
        self.ingest.process_message(content=content, role=role, agent_id=agent_id)
        time.sleep(0.3)

    def _mock_plan(self, search_query):
        plan = RetrievalPlan(
            original_query="test", instructions=[
                RetrievalInstruction(intent="test", search_query=search_query,
                                     keywords=[], time_filter="all")
            ],
        )
        self.planner.generate_plan.return_value = plan

    def _retrieve(self, query, agent_id, score_threshold=0.4):
        self._mock_plan(search_query=query)
        req = RetrievalRequest(query=query, limit=10,
                               agent_id=agent_id, score_threshold=score_threshold)
        return self.retrieve.retrieve(req, agent_id=agent_id)


class TestBasicAgentIsolation(IsolationTestBase):
    """基础 agent_id 隔离"""

    def test_write_isolation_different_agents_retrieve_nothing(self):
        """agent A 写入，agent B 检索为空"""
        self._write("张三的密码是 123456", agent_id="zhang_san")
        self._write("张三喜欢吃烤肉", agent_id="zhang_san")

        result = self._retrieve("密码是什么", agent_id="li_si")
        contents = [r.item.content for r in result.results]
        assert len(contents) == 0, f"李四不应检索到张三的记忆，实际: {contents}"

    def test_same_content_different_agents_no_cross_contamination(self):
        """两个 agent 各写同名内容，互不干扰"""
        self._write("我喜欢喝咖啡", agent_id="zhang_san")
        self._write("我喜欢喝咖啡", agent_id="li_si")
        # 额外写入不同内容以区分
        self._write("张三的秘密", agent_id="zhang_san")
        self._write("李四的秘密", agent_id="li_si")

        # 李四检索
        result_li = self._retrieve("秘密", agent_id="li_si")
        contents_li = [r.item.content for r in result_li.results]
        assert any("李四" in c for c in contents_li), f"李四应检索到自己的秘密: {contents_li}"
        assert not any("张三" in c for c in contents_li), f"李四不应检索到张三的秘密: {contents_li}"

        # 张三检索
        result_zs = self._retrieve("秘密", agent_id="zhang_san")
        contents_zs = [r.item.content for r in result_zs.results]
        assert any("张三" in c for c in contents_zs), f"张三应检索到自己的秘密: {contents_zs}"
        assert not any("李四" in c for c in contents_zs), f"张三不应检索到李四的秘密: {contents_zs}"

    def test_agent_reset_only_affects_target(self):
        """重置 agent A 不影响 agent B"""
        self._write("张三的记忆", agent_id="zhang_san")
        self._write("李四的记忆", agent_id="li_si")

        # 只删除张三的数据（同时清 SQLite 和 mock vector）
        with self.sqlite._get_connection() as conn:
            conn.execute("DELETE FROM episodic_memory WHERE agent_id = ?", ("zhang_san",))
            conn.commit()
        self.mock_vector.items = {
            k: v for k, v in self.mock_vector.items.items()
            if v.agent_id != "zhang_san"
        }

        # 李四的数据还在
        result_li = self._retrieve("记忆", agent_id="li_si")
        assert len(result_li.results) >= 1, "李四的记忆应还在"

        # 张三的数据已删除
        result_zs = self._retrieve("记忆", agent_id="zhang_san")
        assert len(result_zs.results) == 0, "张三的记忆应已删除"


class TestMemoryTypeIsolation(IsolationTestBase):
    """不同 memory_type 不跨 agent 泄露"""

    def test_query_memory_not_leak_to_other_agent_assembler(self):
        """agent A 的问句不会出现在 agent B 的 PromptAssembler 输出中"""
        # 张三写入问句
        self._write("我昨天帮你做了什么？", agent_id="zhang_san")
        # 李四写入事件
        self._write("我前天帮你修了发电机", agent_id="li_si")

        # 李四检索
        result = self._retrieve("我做了什么", agent_id="li_si")
        messages = self.assembler.assemble("我做了什么", result)
        system_content = messages[0]["content"]

        # 李四的上下文不应包含张三的问句
        assert "昨天帮你做了什么" not in system_content.replace(" ", ""), (
            f"李四的上下文不应包含张三的问句: {system_content[:300]}"
        )
        # 李四的上下文应包含自己的事件
        assert "发电机" in system_content, (
            f"李四的上下文应包含自己的事件记忆: {system_content[:300]}"
        )

    def test_event_memory_not_leak_to_other_agent(self):
        """agent A 的事件记忆不出现在 agent B 的 factual_event_memory 区"""
        self._write("张三修好了发电机", agent_id="zhang_san")
        self._write("李四买了水", agent_id="li_si")

        # 李四检索
        result = self._retrieve("买了什么", agent_id="li_si")
        messages = self.assembler.assemble("买了什么", result)
        system_content = messages[0]["content"]

        assert "李四" in system_content or "买了水" in system_content, (
            f"李四应看到自己的记忆: {system_content[:200]}"
        )
        assert "张三" not in system_content, (
            f"李四不应看到张三的记忆: {system_content[:200]}"
        )


class TestStorageLayerIsolation(IsolationTestBase):
    """存储层（SQLite + VectorStore）隔离验证"""

    def test_sqlite_search_respects_agent_id(self):
        """SQLite search_text 按 agent_id 过滤"""
        item_a = MemoryItem(id="a1", content="A的记忆", role=MemoryRole.USER,
                            agent_id="agent_a", metadata=MemoryMetadata())
        item_b = MemoryItem(id="b1", content="B的记忆", role=MemoryRole.USER,
                            agent_id="agent_b", metadata=MemoryMetadata())
        self.sqlite.add(item_a)
        self.sqlite.add(item_b)

        results_a = self.sqlite.search_text(query="记忆", agent_id="agent_a")
        assert len(results_a) == 1
        assert results_a[0].agent_id == "agent_a"

        results_b = self.sqlite.search_text(query="记忆", agent_id="agent_b")
        assert len(results_b) == 1
        assert results_b[0].agent_id == "agent_b"

    def test_vector_store_search_respects_agent_id(self):
        """MockVectorStore search 按 agent_id 过滤"""
        item_a = MemoryItem(id="va1", content="A向量记忆", role=MemoryRole.USER,
                            agent_id="agent_a", metadata=MemoryMetadata())
        item_b = MemoryItem(id="vb1", content="B向量记忆", role=MemoryRole.USER,
                            agent_id="agent_b", metadata=MemoryMetadata())
        self.mock_vector.add(item_a)
        self.mock_vector.add(item_b)

        results_a = self.mock_vector.search(query="向量", agent_id="agent_a")
        assert all(r.item.agent_id == "agent_a" for r in results_a)

        results_b = self.mock_vector.search(query="向量", agent_id="agent_b")
        assert all(r.item.agent_id == "agent_b" for r in results_b)

    def test_sqlite_list_recent_returns_all_agents(self):
        """list_recent 不过滤 agent（预期行为：管理接口）"""
        self.sqlite.add(MemoryItem(id="r1", content="A最近", role=MemoryRole.USER,
                                    agent_id="agent_a", metadata=MemoryMetadata()))
        self.sqlite.add(MemoryItem(id="r2", content="B最近", role=MemoryRole.USER,
                                    agent_id="agent_b", metadata=MemoryMetadata()))

        all_recent = self.sqlite.list_recent(limit=10)
        agent_ids = {r.agent_id for r in all_recent}
        assert "agent_a" in agent_ids
        assert "agent_b" in agent_ids
