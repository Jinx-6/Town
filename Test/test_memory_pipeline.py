"""
记忆处理流程专项测试
覆盖：写入持久化 / 历史检索 / 上下文拼接 / 数据隔离 / 会话污染
无 Neo4j、无真实 LLM 调用、无 BGE 模型依赖
"""
import sys
import os
import time
import tempfile
import shutil
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from Memory.schema.memory_item import MemoryItem, MemoryRole, MemoryStage
from Memory.schema.routing import BackendTarget
from Memory.schema.retrieval import (
    RetrievalRequest, RetrievedMemory, RetrievalResponse
)
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
# Mock 向量库 — 不依赖 ChromaDB / BGE 模型
# ============================================================
class MockVectorStore:
    """模拟 L2 向量库，基于 agent_id 过滤，完全可控"""

    def __init__(self):
        self.items: dict[str, MemoryItem] = {}

    def add(self, item: MemoryItem):
        self.items[item.id] = item

    def search(self, query: str, agent_id: str, limit: int = 5) -> list[RetrievedMemory]:
        results = []
        for item in self.items.values():
            if item.agent_id == agent_id:
                results.append(RetrievedMemory(
                    item=item, score=0.85, source=BackendTarget.VECTOR
                ))
        return results[:limit]


# ============================================================
# 测试夹具
# ============================================================
class TestMemoryPipeline:
    """记忆处理流程 5 项专项测试"""

    def setup_method(self):
        """每个测试用例前：创建临时目录，组装记忆系统"""
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test.db")

        # 存储层
        self.cache = WorkingMemoryCache()
        self.sqlite = SQLiteLogStorage(db_path=self.db_path)
        self.mock_vector = MockVectorStore()

        # 策略层
        self.router = WriteRouter()
        self.update_policy = UpdatePolicy()
        self.scorer = MemoryScorer()
        self.retrieval_policy = RetrievalPolicy()

        # 调度层
        self.backends = {
            BackendTarget.SQLITE: self.sqlite,
            BackendTarget.VECTOR: self.mock_vector,
        }
        self.dispatcher = AsyncDispatcher(
            backends=self.backends,
            extractor=None,
            conflict_resolver=None,
            index_manager=None,
            update_policy=self.update_policy,
        )

        # 业务中枢
        self.ingest = IngestHub(
            working_cache=self.cache, router=self.router, dispatcher=self.dispatcher
        )
        self.planner = MagicMock()
        self.retrieve = RetrieveHub(
            planner=self.planner,
            cache=self.cache,
            sqlite=self.sqlite,
            vector_store=self.mock_vector,
            graph_store=None,
            scorer=self.scorer,
            policy=self.retrieval_policy,
        )

        self.assembler = PromptAssembler(agent_name="张三")

    def teardown_method(self):
        """每个测试用例后：清理临时目录"""
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ── 辅助方法 ────────────────────────────────────────
    def _write_and_wait(self, content: str, agent_id: str, role=MemoryRole.USER):
        """写入记忆并等待异步分发完成"""
        self.ingest.process_message(content=content, role=role, agent_id=agent_id)
        time.sleep(0.3)

    def _mock_plan(self, search_query: str):
        """为 planner.generate_plan 设定固定返回值，不调用 LLM"""
        plan = RetrievalPlan(
            original_query="test query",
            instructions=[
                RetrievalInstruction(
                    intent="test",
                    search_query=search_query,
                    keywords=[],
                    time_filter="all",
                )
            ],
        )
        self.planner.generate_plan.return_value = plan

    # ============================================================
    # 测试 1：记忆写入持久化
    # ============================================================
    def test_write_persistence_sqlite(self):
        """录入记忆后，数据稳定存储至 SQLite"""
        content = "玩家昨日帮张三修理好发电机"
        self._write_and_wait(content, agent_id="zhang_san")

        # 从 SQLite 回读
        records = self.sqlite.list_recent(limit=10)
        assert len(records) >= 1, "SQLite 中应至少有一条记录"
        stored = records[0]
        assert stored.content == content
        assert stored.role == MemoryRole.USER

    def test_write_persistence_vector(self):
        """录入记忆后，数据同步写入向量库"""
        content = "玩家昨日帮张三修理好发电机"
        self._write_and_wait(content, agent_id="zhang_san")

        assert len(self.mock_vector.items) >= 1, "Mock 向量库中应有一条记录"

    # ============================================================
    # 测试 2：历史记忆检索 (不同措辞)
    # ============================================================
    def test_retrieval_different_wording(self):
        """用不同措辞查询，仍能检索到预设记忆与关键信息"""
        # 预先存入
        self._write_and_wait("玩家昨日帮张三修理好发电机", agent_id="zhang_san")
        self._write_and_wait("张三喜欢吃红烧肉", agent_id="zhang_san")

        # 用不同措辞检索 — 模拟 planner 返回的 search_query
        self._mock_plan(search_query="修理 帮忙 发电机 帮忙做过什么事")

        req = RetrievalRequest(query="你还记得昨天我帮你做过什么事吗", limit=5, agent_id="zhang_san")
        result = self.retrieve.retrieve(req, agent_id="zhang_san")

        # 断言：至少召回一条含"发电机"的记忆
        contents = [r.item.content for r in result.results]
        found = any("发电机" in c for c in contents)
        assert found, f"检索结果中应包含'发电机'相关记忆，实际: {contents}"

    # ============================================================
    # 测试 3：智能体记忆调用逻辑 — 记忆被并入提示上下文
    # ============================================================
    def test_memory_assembled_into_context(self):
        """生成回复前，检索结果被 PromptAssembler 拼入 LLM 上下文"""
        # 构造一条已知记忆
        item = MemoryItem(
            id="mem_001",
            content="进喜说他最喜欢的语言是 Python",
            role=MemoryRole.USER,
            agent_id="zhang_san",
        )
        retrieved = RetrievedMemory(item=item, score=0.92, source=BackendTarget.VECTOR)

        response = RetrievalResponse(
            original_query="我喜欢的语言是什么",
            agent_id="zhang_san",
            results=[retrieved],
        )

        messages = self.assembler.assemble("我喜欢的语言是什么", response)

        # messages 格式: [{"role":"system","content":"..."}, {"role":"user","content":"..."}]
        system_content = messages[0]["content"]
        assert "Python" in system_content, (
            f"组装后的 system prompt 应包含记忆中的 'Python'，实际:\n{system_content}"
        )

    # ============================================================
    # 测试 4：智能体数据隔离
    # ============================================================
    def test_agent_data_isolation(self):
        """张三的记忆不应被李四检索到"""
        # 张三的记忆
        self._write_and_wait("张三的密码是 123456", agent_id="zhang_san")
        # 李四的记忆
        self._write_and_wait("李四喜欢喝咖啡", agent_id="li_si")

        # 以李四身份检索
        self._mock_plan(search_query="密码")
        req = RetrievalRequest(query="密码是什么", limit=5, agent_id="li_si")
        result = self.retrieve.retrieve(req, agent_id="li_si")

        contents = [r.item.content for r in result.results]
        leaked = any("zhang_san" in c or "123456" in c or "张三" in c for c in contents)
        assert not leaked, f"李四不应检索到张三的数据，实际: {contents}"

    # ============================================================
    # 测试 5：会话信息污染校验（已修复）
    # ============================================================
    def test_no_session_pollution_after_fix(self):
        """
        验证正确流程：先检索 → 生成回复 → 再存储。
        生成回复前，本轮用户输入不应出现在检索结果中。
        """
        # 预存一条旧记忆
        self._write_and_wait("昨天我们一起修好了发电机", agent_id="zhang_san")

        # 本轮新输入：先检索，不预先写入
        current_input = "你还记得昨天我帮你做过什么事吗"
        self._mock_plan(search_query="昨天 帮忙 修 发电机")

        req = RetrievalRequest(query=current_input, limit=5, agent_id="zhang_san")
        result = self.retrieve.retrieve(req, agent_id="zhang_san")

        contents = [r.item.content for r in result.results]

        # 断言 1：当前输入不应出现在检索结果中
        polluted = any(current_input in c for c in contents)
        assert not polluted, (
            f"污染未修复！本轮输入不应被当作历史记忆，实际: {contents}"
        )

        # 断言 2：但旧记忆应该被检索到
        assert any("发电机" in c for c in contents), (
            f"旧记忆应被检索到，实际: {contents}"
        )

        # 断言 3：旧记忆检索到了，且没有混入当前输入
        old_found = any("发电机" in c for c in contents)
        assert old_found and not polluted, "检索应命中旧记忆但不含当前输入"

    def test_current_input_stored_after_response(self):
        """
        验证：生成回复后，当前输入确实被写入记忆库供后续使用。
        """
        # 模拟修复后的流程：先检索，回复后再存储
        current_input = "我叫进喜"
        self._mock_plan(search_query="进喜")

        # Step 1: 回复前检索 — 不应该有当前输入
        req = RetrievalRequest(query=current_input, limit=5, agent_id="zhang_san", score_threshold=0.5)
        result_before = self.retrieve.retrieve(req, agent_id="zhang_san")
        contents_before = [r.item.content for r in result_before.results]
        assert not any(current_input in c for c in contents_before), "回复前不应检索到当前输入"

        # Step 2: 生成回复后，将本轮对话写入
        assistant_reply = "你好进喜，很高兴认识你"
        self._write_and_wait(current_input, agent_id="zhang_san")
        self._write_and_wait(assistant_reply, agent_id="zhang_san", role=MemoryRole.ASSISTANT)
        time.sleep(0.5)  # 确保异步分发线程全部写入完毕

        # Step 3: 写入后应当可以被检索到
        req2 = RetrievalRequest(query=current_input, limit=5, agent_id="zhang_san", score_threshold=0.5)
        result_after = self.retrieve.retrieve(req2, agent_id="zhang_san")
        contents_after = [r.item.content for r in result_after.results]
        assert any(current_input in c for c in contents_after), (
            f"写入后应能检索到当前输入，实际: {contents_after}"
        )

    # ============================================================
    # 测试 6：时间关键词检索 — "昨天"优先于"前天"
    # ============================================================
    def test_time_keyword_yesterday_first(self):
        """查询含'昨天'时，时间匹配的昨天记忆应排在第一位"""
        from demo_cli import apply_time_boost
        from datetime import timedelta
        now = datetime.now(timezone.utc)

        yesterday = now - timedelta(days=1)
        day_before = now - timedelta(days=2)

        item_gen = MemoryItem(
            id="mem_yesterday", content="玩家昨天帮张三修理发电机",
            role=MemoryRole.USER, agent_id="zhang_san", timestamp=yesterday,
        )
        item_water = MemoryItem(
            id="mem_day_before", content="玩家前天帮张三买水",
            role=MemoryRole.USER, agent_id="zhang_san", timestamp=day_before,
        )

        # 模拟检索返回两条得分相同的记忆（买水在前，发电机在后）
        results = [
            RetrievedMemory(item=item_water, score=0.75, source=BackendTarget.VECTOR),
            RetrievedMemory(item=item_gen, score=0.75, source=BackendTarget.VECTOR),
        ]

        boosted = apply_time_boost("你还记得我昨天帮你做了什么吗", results)

        assert "发电机" in boosted[0].item.content, (
            f"查询'昨天'时，第一条结果应为发电机，实际: {boosted[0].item.content}"
        )

    def test_time_keyword_day_before_yesterday_first(self):
        """查询含'前天'时，时间匹配的前天记忆应排在第一位"""
        from demo_cli import apply_time_boost
        from datetime import timedelta
        now = datetime.now(timezone.utc)

        yesterday = now - timedelta(days=1)
        day_before = now - timedelta(days=2)

        item_gen = MemoryItem(
            id="mem_yesterday", content="玩家昨天帮张三修理发电机",
            role=MemoryRole.USER, agent_id="zhang_san", timestamp=yesterday,
        )
        item_water = MemoryItem(
            id="mem_day_before", content="玩家前天帮张三买水",
            role=MemoryRole.USER, agent_id="zhang_san", timestamp=day_before,
        )

        # 模拟检索返回两条得分相同的记忆（发电机在前，买水在后）
        results = [
            RetrievedMemory(item=item_gen, score=0.75, source=BackendTarget.VECTOR),
            RetrievedMemory(item=item_water, score=0.75, source=BackendTarget.VECTOR),
        ]

        boosted = apply_time_boost("你还记得我前天帮你做了什么吗", results)

        assert "买水" in boosted[0].item.content, (
            f"查询'前天'时，第一条结果应为买水，实际: {boosted[0].item.content}"
        )

    # ============================================================
    # 测试 7：时间冲突过滤 + 噪音问句排除
    # ============================================================
    def test_time_conflict_and_noise_filtering(self):
        """查询'前天'时：饮水机排第一，问句噪音被排除"""
        from demo_cli import apply_time_boost
        from datetime import timedelta
        now = datetime.now(timezone.utc)

        item_gen = MemoryItem(
            id="mem_gen", content="我昨天帮你修了发电机",
            role=MemoryRole.USER, agent_id="zhang_san",
            timestamp=now - timedelta(days=1),
        )
        item_water = MemoryItem(
            id="mem_water", content="我前天帮你修了饮水机",
            role=MemoryRole.USER, agent_id="zhang_san",
            timestamp=now - timedelta(days=2),
        )
        item_noise = MemoryItem(
            id="mem_noise", content="我昨天帮你做了什么",
            role=MemoryRole.USER, agent_id="zhang_san",
            timestamp=now - timedelta(days=1),
        )

        # 模拟检索返回三条（噪音排第一，饮水机排最后）
        results = [
            RetrievedMemory(item=item_noise, score=0.80, source=BackendTarget.VECTOR),
            RetrievedMemory(item=item_gen, score=0.75, source=BackendTarget.VECTOR),
            RetrievedMemory(item=item_water, score=0.70, source=BackendTarget.VECTOR),
        ]

        boosted = apply_time_boost("我前天帮你做了什么", results)

        # 断言 1：第一条结果包含"饮水机"
        assert "饮水机" in boosted[0].item.content, (
            f"查询'前天'时，第一条结果应为饮水机，实际: {boosted[0].item.content}"
        )

        # 断言 2：问句噪音「昨天帮你做了什么」不应排在真实记忆前面
        contents_top2 = [r.item.content for r in boosted[:2]]
        noise_in_top2 = any("昨天帮你做了什么" in c for c in contents_top2)
        assert not noise_in_top2, (
            f"问句噪音不应进入前2条，实际前2: {contents_top2}"
        )


# ============================================================
# 元数据提取回归测试
# ============================================================
class TestMetadataExtraction:
    """验证基于规则的元数据提取正确性"""

    def test_extract_event_with_time(self):
        """'我前天帮你修了饮水机' → event + repair + 前天"""
        from Memory.processor.metadata_extractor import extract_metadata
        meta = extract_metadata("我前天帮你修了饮水机")
        assert meta["memory_type"] == "event"
        assert meta["is_factual_memory"] is True
        assert meta["relative_time"] == "前天"
        assert meta["temporal_text"] == "前天"
        assert meta["event_type"] == "repair"
        assert "饮水机" in meta["keywords"]

    def test_extract_query_detection(self):
        """'我昨天帮你做了什么？' → query + is_factual_memory=false"""
        from Memory.processor.metadata_extractor import extract_metadata
        meta = extract_metadata("我昨天帮你做了什么？")
        assert meta["memory_type"] == "query"
        assert meta["is_factual_memory"] is False
        assert meta["event_type"] == "ask"

    def test_extract_buy_event(self):
        """'我昨天帮你买了一瓶水' → event + buy + 昨天"""
        from Memory.processor.metadata_extractor import extract_metadata
        meta = extract_metadata("我昨天帮你买了一瓶水")
        assert meta["memory_type"] == "event"
        assert meta["is_factual_memory"] is True
        assert meta["relative_time"] == "昨天"
        assert meta["event_type"] == "buy"

    def test_extract_no_time(self):
        """无时间词的内容 → relative_time 为空字符串"""
        from Memory.processor.metadata_extractor import extract_metadata
        meta = extract_metadata("进喜说他最喜欢的语言是 Python")
        assert meta["relative_time"] == ""
        assert meta["memory_type"] == "event"
        assert meta["is_factual_memory"] is True

    def test_existing_meta_not_overwritten(self):
        """用户显式传入的元数据不被覆盖"""
        from Memory.processor.metadata_extractor import extract_metadata
        existing = {"memory_type": "preference", "is_factual_memory": True}
        meta = extract_metadata("我昨天帮你做了什么？", existing_meta=existing)
        assert meta["memory_type"] == "preference"  # 保持原值
        assert meta["is_factual_memory"] is True    # 保持原值
        assert meta["relative_time"] == "昨天"      # 补充缺失字段


class TestMetadataInPipeline:
    """验证元数据在记忆流水线中端到端生效"""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test_meta.db")
        self.cache = WorkingMemoryCache()
        self.sqlite = SQLiteLogStorage(db_path=self.db_path)
        self.mock_vector = MockVectorStore()
        self.router = WriteRouter()
        self.update_policy = UpdatePolicy()
        self.scorer = MemoryScorer()
        self.retrieval_policy = RetrievalPolicy()
        self.backends = {
            BackendTarget.SQLITE: self.sqlite,
            BackendTarget.VECTOR: self.mock_vector,
        }
        self.dispatcher = AsyncDispatcher(
            backends=self.backends, extractor=None, conflict_resolver=None,
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
        self.assembler = PromptAssembler(agent_name="张三")

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write(self, content, agent_id="zhang_san", role=MemoryRole.USER):
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

    def test_query_memory_excluded_from_context(self):
        """问句记忆不应出现在 PromptAssembler 上下文中"""
        # 存入一条问句 + 一条事件
        self._write("我昨天帮你做了什么？", agent_id="zhang_san")
        self._write("我昨天帮你修了发电机", agent_id="zhang_san")

        self._mock_plan(search_query="昨天 帮 修 发电机")
        req = RetrievalRequest(query="我昨天帮你做了什么", limit=5,
                               agent_id="zhang_san")
        result = self.retrieve.retrieve(req, agent_id="zhang_san")

        # PromptAssembler 应只包含 factual 记忆
        messages = self.assembler.assemble(req.query, result)
        system_content = messages[0]["content"]

        assert "发电机" in system_content, (
            f"事件记忆应出现在上下文中，实际: {system_content[:200]}"
        )
        assert "我昨天帮你做了什么？" not in system_content.replace(" ", ""), (
            f"问句记忆不应出现在上下文中，实际: {system_content[:200]}"
        )

    def test_old_memory_without_metadata_still_works(self):
        """旧记忆（无新元数据字段）仍能正常检索与组装"""
        item = MemoryItem(
            id="old_no_meta", content="玩家昨天帮张三修好了发电机",
            role=MemoryRole.USER, agent_id="zhang_san",
            # metadata 用默认值（新字段均为默认值）
        )
        retrieved = RetrievedMemory(
            item=item, score=0.85, source=BackendTarget.VECTOR
        )
        response = RetrievalResponse(
            original_query="昨天帮了什么", agent_id="zhang_san",
            results=[retrieved],
        )
        messages = self.assembler.assemble("昨天帮了什么", response)
        system_content = messages[0]["content"]
        # 旧记忆 is_factual_memory 默认为 True，应正常出现
        assert "发电机" in system_content, (
            f"旧记忆应正常出现在上下文中，实际: {system_content[:200]}"
        )

    def test_query_memory_still_stored_but_not_assembled(self):
        """问句记忆被存储（is_factual_memory=False），但不被拼入上下文"""
        self._write("我前天帮你修了饮水机", agent_id="zhang_san")
        self._write("我昨天帮你做了什么？", agent_id="zhang_san")

        # 验证 SQLite 中两条都在
        records = self.sqlite.list_recent(limit=10)
        assert len(records) >= 2

        # 检索「前天」
        self._mock_plan(search_query="前天 修 饮水机")
        req = RetrievalRequest(query="我前天帮你做了什么", limit=5,
                               agent_id="zhang_san")
        result = self.retrieve.retrieve(req, agent_id="zhang_san")

        # 拼入上下文 — 问句不应出现
        messages = self.assembler.assemble(req.query, result)
        system_content = messages[0]["content"]

        assert "饮水机" in system_content, (
            f"前天的事件记忆应出现在上下文中"
        )
        noise_free = "昨天帮你做了什么" not in system_content.replace(" ", "")
        assert noise_free, (
            f"问句不应出现在上下文中，实际: {system_content[:200]}"
        )

    def test_factual_recall_excludes_query_from_events(self):
        """事实召回：事件记忆在 factual_event_memory 区，问句在 previous_user_questions 区"""
        self._write("我前天帮你修了饮水机", agent_id="zhang_san")
        self._write("我昨天帮你做了什么？", agent_id="zhang_san")

        self._mock_plan(search_query="前天 帮 修 饮水机")
        req = RetrievalRequest(query="我前天帮你做了什么？", limit=5,
                               agent_id="zhang_san", score_threshold=0.4)
        result = self.retrieve.retrieve(req, agent_id="zhang_san")

        from demo_cli import apply_time_boost, detect_query_intent
        intent = detect_query_intent("我前天帮你做了什么？")
        assert intent == "factual_event_recall", f"应为 factual_event_recall，实际: {intent}"

        apply_time_boost("我前天帮你做了什么？", result.results)
        messages = self.assembler.assemble(req.query, result)
        system_content = messages[0]["content"]

        # 断言 1: factual_event_memory 区包含饮水机事件
        assert "饮水机" in system_content, f"事件应在 factual 区: {system_content[:300]}"

        # 断言 2: 如果 factual_event_memory 区存在，不包含问句
        if "<factual_event_memory>" in system_content:
            fact_section_start = system_content.find("<factual_event_memory>")
            next_section = system_content.find("<previous_user_questions>", fact_section_start)
            if next_section == -1:
                next_section = system_content.find("<task_state>", fact_section_start)
            if next_section == -1:
                next_section = len(system_content)
            fact_section = system_content[fact_section_start:next_section]
            assert "昨天帮你做了什么" not in fact_section.replace(" ", ""), (
                f"问句不应在 factual_event_memory 区: {fact_section[:200]}"
            )

    def test_interaction_history_recall_includes_queries(self):
        """交互历史召回：问句应出现在 previous_user_questions 区"""
        from demo_cli import detect_query_intent

        # 验证意图检测
        intent1 = detect_query_intent("我之前问过你什么？")
        assert intent1 == "interaction_history_recall", f"应为 interaction_history_recall，实际: {intent1}"

        intent2 = detect_query_intent("我之前聊过类似的问题吗")
        assert intent2 == "interaction_history_recall", f"应为 interaction_history_recall，实际: {intent2}"

    def test_query_intent_factual_vs_interaction(self):
        """对比：事实召回 vs 交互历史召回的意图检测"""
        from demo_cli import detect_query_intent

        # 事实召回
        assert detect_query_intent("你还记得我昨天帮你做了什么吗") == "factual_event_recall"
        assert detect_query_intent("我做了什么") == "factual_event_recall"
        assert detect_query_intent("帮我做了什么") == "factual_event_recall"

        # 交互历史
        assert detect_query_intent("我之前问过什么") == "interaction_history_recall"
        assert detect_query_intent("上次聊过什么") == "interaction_history_recall"

        # 通用
        assert detect_query_intent("你好") == "general_recall"
        assert detect_query_intent("天气怎么样") == "general_recall"


class TestSQLiteRowCompat:
    """验证 _row_to_item 对旧 schema / 脏数据的容错能力"""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "legacy.db")

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_old_schema_no_agent_id_column(self):
        """旧数据库缺少 agent_id 列时，读操作不应崩溃"""
        # 1. 手工建旧表（无 agent_id 列）
        raw_conn = __import__("sqlite3").connect(self.db_path)
        raw_conn.execute("""
            CREATE TABLE episodic_memory (
                id TEXT, content TEXT, role TEXT, timestamp TEXT, stage TEXT,
                metadata_json TEXT, vector_json TEXT
            )
        """)
        raw_conn.execute(
            "INSERT INTO episodic_memory VALUES (?,?,?,?,?,?,?)",
            ("old_1", "旧数据无agent_id列", "user", "2025-01-01T00:00:00", "episodic",
             '{"importance":5}', None)
        )
        raw_conn.commit()
        raw_conn.close()

        # 2. SQLiteLogStorage 启动时自动 ALTER TABLE 加列
        store = SQLiteLogStorage(db_path=self.db_path)
        results = store.search_text(query="旧数据", limit=5)
        assert len(results) == 1
        assert results[0].content == "旧数据无agent_id列"
        assert results[0].agent_id == "default_agent"
        assert results[0].metadata.importance == 5

    def test_corrupt_metadata_json(self):
        """metadata_json 为无效 JSON 时，应返回空 dict 而非崩溃"""
        raw_conn = __import__("sqlite3").connect(self.db_path)
        raw_conn.execute("""
            CREATE TABLE episodic_memory (
                id TEXT, content TEXT, role TEXT, timestamp TEXT, stage TEXT,
                agent_id TEXT DEFAULT 'default_agent', metadata_json TEXT, vector_json TEXT
            )
        """)
        raw_conn.execute(
            "INSERT INTO episodic_memory VALUES (?,?,?,?,?,?,?,?)",
            ("bad_1", "metadata坏了", "user", "2025-01-01T00:00:00", "episodic",
             "zhang_san", "{not valid json!!!", None)
        )
        raw_conn.commit()
        raw_conn.close()

        store = SQLiteLogStorage(db_path=self.db_path)
        results = store.search_text(query="metadata", limit=5)
        assert len(results) == 1
        assert results[0].content == "metadata坏了"
        assert results[0].metadata.importance == 0  # 降级为默认 Metadata

    def test_corrupt_vector_json(self):
        """vector_json 为无效 JSON 时，应返回 None 而非崩溃"""
        raw_conn = __import__("sqlite3").connect(self.db_path)
        raw_conn.execute("""
            CREATE TABLE episodic_memory (
                id TEXT, content TEXT, role TEXT, timestamp TEXT, stage TEXT,
                agent_id TEXT DEFAULT 'default_agent', metadata_json TEXT, vector_json TEXT
            )
        """)
        raw_conn.execute(
            "INSERT INTO episodic_memory VALUES (?,?,?,?,?,?,?,?)",
            ("vec_1", "vector坏了", "user", "2025-01-01T00:00:00", "episodic",
             "zhang_san", "{}", "{bad vector")
        )
        raw_conn.commit()
        raw_conn.close()

        store = SQLiteLogStorage(db_path=self.db_path)
        results = store.search_text(query="vector", limit=5)
        assert len(results) == 1
        assert results[0].content == "vector坏了"
        assert results[0].vector is None

    def test_null_metadata_and_vector(self):
        """metadata_json / vector_json 为 NULL 时不应崩溃"""
        raw_conn = __import__("sqlite3").connect(self.db_path)
        raw_conn.execute("""
            CREATE TABLE episodic_memory (
                id TEXT, content TEXT, role TEXT, timestamp TEXT, stage TEXT,
                agent_id TEXT DEFAULT 'default_agent', metadata_json TEXT, vector_json TEXT
            )
        """)
        raw_conn.execute(
            "INSERT INTO episodic_memory VALUES (?,?,?,?,?,?,?,?)",
            ("null_1", "全部null", "user", "2025-01-01T00:00:00", "episodic",
             "zhang_san", None, None)
        )
        raw_conn.commit()
        raw_conn.close()

        store = SQLiteLogStorage(db_path=self.db_path)
        results = store.list_recent(limit=5)
        assert len(results) == 1
        assert results[0].content == "全部null"
        assert results[0].metadata.importance == 0  # 降级为默认 Metadata
        assert results[0].vector is None
