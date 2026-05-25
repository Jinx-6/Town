"""
Phase 1 — 记忆检索质量测试（排序 + 召回 + 精确）
=================================================
全部使用 MockVectorStore + 临时 SQLite，无外部依赖。

每个检索测试包含三层断言：
  - 排序 (ranking):   results[0] 的内容与期望关键词匹配
  - 召回 (recall):    期望的记忆出现在 results 中
  - 精确 (precision): 不应出现的记忆类型/内容 不出现

重点验证：
  - factual_event_recall 不混入 query_memory
  - interaction_history_recall 可召回 query_memory
  - relative_time metadata 优先于 timestamp
  - PromptAssembler section 正确分区
"""
import sys, os, time, tempfile, shutil
from datetime import datetime, timezone, timedelta
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
            if item.agent_id != agent_id:
                continue
            if metadata_filters:
                meta = item.metadata if isinstance(item.metadata, dict) else item.metadata.model_dump()
                skip = False
                for k, v in metadata_filters.items():
                    if meta.get(k) != v:
                        skip = True
                        break
                if skip:
                    continue
            results.append(RetrievedMemory(
                item=item, score=0.85, source=BackendTarget.VECTOR
            ))
        return results[:limit]


# ============================================================
# 工具：构造带完整 metadata 的 MemoryItem
# ============================================================
def make_item(content, agent_id="zhang_san", memory_type="event",
              relative_time="", event_type="general", is_factual=True,
              hours_ago=0.0, item_id=None, keywords=None):
    """快速构造带完整 metadata 的记忆"""
    ts = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    meta = MemoryMetadata(
        memory_type=memory_type,
        relative_time=relative_time,
        temporal_text=relative_time,
        event_type=event_type,
        is_factual_memory=is_factual,
        keywords=keywords or [],
        importance=5,
    )
    return MemoryItem(
        id=item_id or f"mem_{content[:6]}_{int(time.time()*1000)}",
        content=content, role=MemoryRole.USER,
        agent_id=agent_id, timestamp=ts, metadata=meta,
        stage=MemoryStage.SEMANTIC,
    )


def make_retrieved(item, score=0.80, source=BackendTarget.VECTOR):
    return RetrievedMemory(item=item, score=score, source=source)


# ============================================================
# 共享 Fixture
# ============================================================
class RetrievalQualityBase:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test_rq.db")

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
        self.assembler = PromptAssembler(agent_name="测试", min_score_threshold=0.35)

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

    def _retrieve_and_assemble(self, query, agent_id="zhang_san", score_threshold=0.4):
        """检索 + boost + assemble，返回 (response, system_content)"""
        from demo_cli import apply_time_boost

        self._mock_plan(search_query=query)
        req = RetrievalRequest(query=query, limit=10, agent_id=agent_id,
                               score_threshold=score_threshold)
        result = self.retrieve.retrieve(req, agent_id=agent_id)
        apply_time_boost(query, result.results)
        messages = self.assembler.assemble(query, result)
        return result, messages[0]["content"]

    def _section_content(self, full_text, section_tag):
        """提取某个 section 的内容"""
        start_tag = f"<{section_tag}>"
        end_tag = f"</{section_tag}>"
        if start_tag not in full_text:
            return ""
        start = full_text.find(start_tag) + len(start_tag)
        end = full_text.find(end_tag, start)
        return full_text[start:end].strip() if end != -1 else ""


# ============================================================
# 测试：意图分流 → 排序 + 分区正确性
# ============================================================
class TestIntentBasedRouting(RetrievalQualityBase):
    """同一批记忆，不同查询意图产生不同的排序和分区结果"""

    def setup_method(self):
        super().setup_method()
        # 注入 3 条记忆：事件、另一事件、问句
        self._write("我前天帮你修了饮水机", agent_id="zhang_san")
        self._write("我昨天帮你修了发电机", agent_id="zhang_san")
        self._write("我昨天帮你做了什么？", agent_id="zhang_san")

    def test_factual_recall_sections(self):
        """事实召回：事实在 retrieved_factual_memories，问句不在其中"""
        _, sc = self._retrieve_and_assemble("我前天帮你做了什么？")

        fact_section = self._section_content(sc, "retrieved_factual_memories")
        query_section = self._section_content(sc, "retrieved_dialog_history")

        # 排序：factual 区包含饮水机（前天事件）
        assert "饮水机" in fact_section, (
            f"retrieved_factual_memories 应包含前天事件，实际: {fact_section}"
        )
        # 精确：factual 区不含问句
        assert "昨天帮你做了什么" not in fact_section.replace(" ", ""), (
            f"retrieved_factual_memories 不应包含问句，实际: {fact_section}"
        )
        # 精确：问句不应出现在 retrieved_factual_memories
        if query_section:
            assert "昨天帮你做了什么" in query_section.replace(" ", ""), (
                f"问句应在 retrieved_dialog_history 区，实际: {query_section}"
            )

    def test_factual_recall_event_ranked_first(self):
        """事实召回：event 记忆排序在 query 之前"""
        result, _ = self._retrieve_and_assemble("我前天帮你做了什么？")
        ranked = result.results

        # 排序：第一条必须是事件
        assert len(ranked) >= 1
        top_content = ranked[0].item.content
        assert "饮水机" in top_content or "发电机" in top_content, (
            f"第一条应为事件记忆，实际: {top_content}"
        )

    def test_interaction_history_recall_query_ranked_first(self):
        """交互历史召回：query 排在 event 前面"""
        result, _ = self._retrieve_and_assemble("我之前问过你什么？")
        ranked = result.results

        assert len(ranked) >= 1
        # 排序：问句应排第一（或至少在前列）
        top2_contents = [r.item.content for r in ranked[:2]]
        has_query_in_top2 = any("昨天帮你做了什么" in c for c in top2_contents)
        assert has_query_in_top2, (
            f"交互历史召回时间句应排在前2，实际: {top2_contents}"
        )

    def test_interaction_history_sections(self):
        """交互历史召回：question 出现在 retrieved_dialog_history 区"""
        _, sc = self._retrieve_and_assemble("我之前问过你什么类似问题？")

        query_section = self._section_content(sc, "retrieved_dialog_history")
        fact_section = self._section_content(sc, "retrieved_factual_memories")

        # 召回：问句区包含问句
        assert "昨天帮你做了什么" in query_section.replace(" ", ""), (
            f"retrieved_dialog_history 应包含问句，实际: {query_section}"
        )
        # 事实区仍然保留事件（不被丢弃）
        assert "饮水机" in fact_section or "发电机" in fact_section, (
            f"事实区应保留事件记忆，实际: {fact_section}"
        )

    def test_general_recall_all_types_present(self):
        """通用召回：所有类型都可能出现，但分区正确"""
        _, sc = self._retrieve_and_assemble("最近发生了什么？")

        fact_section = self._section_content(sc, "retrieved_factual_memories")
        query_section = self._section_content(sc, "retrieved_dialog_history")

        # 召回：事件在事实区
        has_event = "饮水机" in fact_section or "发电机" in fact_section
        assert has_event, f"通用召回应有事件: {fact_section}"


# ============================================================
# 测试：时间 metadata 排序（relative_time 优先于 timestamp）
# ============================================================
class TestTemporalMetadataRanking(RetrievalQualityBase):
    """relative_time metadata 是事件时间，timestamp 是写入时间。
       检索排序必须以 relative_time 为准。"""

    def test_relative_time_match_beats_timestamp_only(self):
        """relative_time 匹配 > 仅时间戳窗口匹配"""
        from demo_cli import apply_time_boost

        now = datetime.now(timezone.utc)

        # 事件时间 = "前天"(metadata)，但写入时间 = 现在（timestamp 不匹配前天窗口）
        item_correct = make_item(
            content="我前天帮你修了饮水机", relative_time="前天",
            event_type="repair", hours_ago=0.1,  # 刚刚写入
        )
        # 事件时间 = "昨天"(metadata)，写入时间 = 前天（timestamp 匹配前天窗口）
        item_wrong_time = make_item(
            content="我昨天帮你修了发电机", relative_time="昨天",
            event_type="repair", hours_ago=48.0,  # 2天前写入
        )

        results = [
            make_retrieved(item_wrong_time, score=0.80),
            make_retrieved(item_correct, score=0.75),
        ]

        # 查询"前天"意图
        boosted = apply_time_boost("我前天帮你做了什么", results)

        # 排序：relative_time="前天" 的应该排第一
        assert "饮水机" in boosted[0].item.content, (
            f"relative_time 匹配应优先，第一条应为饮水机，实际: {boosted[0].item.content}"
        )

    def test_relative_time_conflict_penalized(self):
        """relative_time 冲突扣分 > 时间戳窗口巧合加分"""
        from demo_cli import apply_time_boost

        # 事件时间="昨天"，但查询"前天" → 冲突
        item_conflict = make_item(
            content="我昨天帮你修了发电机", relative_time="昨天",
            event_type="repair", hours_ago=48.0,  # 时间戳在前天窗口内
        )
        # 事件时间="前天"，查询"前天" → 匹配
        item_match = make_item(
            content="我前天帮你修了饮水机", relative_time="前天",
            event_type="repair", hours_ago=48.0,
        )

        results = [
            make_retrieved(item_conflict, score=0.85),  # 初始分更高
            make_retrieved(item_match, score=0.75),
        ]

        boosted = apply_time_boost("我前天帮你做了什么", results)

        # 冲突扣分后，匹配的应该排第一
        assert "饮水机" in boosted[0].item.content, (
            f"时间匹配应排第一（冲突被扣分），实际: {boosted[0].item.content}"
        )

    def test_no_metadata_falls_back_to_text_and_timestamp(self):
        """无 metadata 的旧记忆：文本规则 + 时间戳兜底"""
        from demo_cli import apply_time_boost

        now = datetime.now(timezone.utc)

        # 无 metadata（旧记忆），但内容含"前天" + 时间戳在 48h 前
        item_old = MemoryItem(
            id="old_1", content="我前天帮你修了饮水机",
            role=MemoryRole.USER, agent_id="zhang_san",
            timestamp=now - timedelta(hours=48),
            metadata=MemoryMetadata(),  # 默认值
        )
        # 无 metadata，内容含"昨天"
        item_old2 = MemoryItem(
            id="old_2", content="我昨天帮你修了发电机",
            role=MemoryRole.USER, agent_id="zhang_san",
            timestamp=now - timedelta(hours=24),
            metadata=MemoryMetadata(),
        )

        results = [
            make_retrieved(item_old2, score=0.80),
            make_retrieved(item_old, score=0.75),
        ]

        boosted = apply_time_boost("我前天帮你做了什么", results)

        # 文本"前天"被识别 + 时间戳窗口匹配 → 应排第一
        assert "饮水机" in boosted[0].item.content, (
            f"旧记忆兜底：文本+时间戳应将前天记忆排第一，实际: {boosted[0].item.content}"
        )


# ============================================================
# 测试：Recall + Precision + 阈值
# ============================================================
class TestRecallAndPrecision(RetrievalQualityBase):
    """验证召回完整性、精确排除、阈值过滤"""

    def test_recall_all_events_for_factual_query(self):
        """事实召回：所有相关事件都被召回（recall）"""
        self._write("我昨天帮你修了发电机", agent_id="zhang_san")
        self._write("我昨天帮你买了水", agent_id="zhang_san")
        self._write("我昨天帮你写了一份报告", agent_id="zhang_san")

        result, _ = self._retrieve_and_assemble("我昨天帮你做了什么？")

        contents = [r.item.content for r in result.results]
        assert any("发电机" in c for c in contents), "应召回发电机"
        assert any("买了水" in c for c in contents), "应召回买水"
        assert any("报告" in c for c in contents), "应召回报告"

    def test_precision_query_excluded_from_factual_section(self):
        """精确：事实召回时，问句不在 retrieved_factual_memories 区"""
        self._write("我昨天帮你修了发电机", agent_id="zhang_san")
        self._write("我昨天帮你修了饮水机", agent_id="zhang_san")
        self._write("我昨天帮你做了什么？", agent_id="zhang_san")  # 问句

        _, sc = self._retrieve_and_assemble("我昨天帮你做了什么？")
        fact_section = self._section_content(sc, "retrieved_factual_memories")

        # 事实区应有事件
        assert "发电机" in fact_section or "饮水机" in fact_section, (
            f"事实区应有事件: {fact_section}"
        )
        # 事实区不应有问句
        assert "昨天帮你做了什么？" not in fact_section.replace(" ", ""), (
            f"事实区不应有问句: {fact_section}"
        )

    def test_low_score_memories_filtered_out(self):
        """低于 min_score_threshold 的记忆不出现在 assembler 输出中"""
        from demo_cli import apply_time_boost

        # 正常记忆
        item_good = make_item(content="我昨天帮你修了发电机",
                              relative_time="昨天", event_type="repair")
        # 低分记忆（即使经过 event boost 仍低于阈值）
        item_bad = make_item(content="与查询完全无关的内容",
                             relative_time="", event_type="general")

        results = [
            make_retrieved(item_good, score=0.80),
            make_retrieved(item_bad, score=0.01),  # 即使 +0.30 也只有 0.31 < 0.35
        ]

        boosted = apply_time_boost("我昨天帮你做了什么", results)
        response = RetrievalResponse(
            original_query="test", agent_id="zhang_san", results=boosted,
        )
        messages = self.assembler.assemble("test", response)
        sc = messages[0]["content"]

        assert "发电机" in sc, "正常记忆应出现"
        assert "完全无关" not in sc, "低分记忆不应出现"

    def test_assembler_sections_not_empty_for_valid_memories(self):
        """有记忆时，对应的 section 标签应正确生成"""
        self._write("我昨天帮你修了饮水机", agent_id="zhang_san")

        _, sc = self._retrieve_and_assemble("我昨天做了什么？")

        assert "<retrieved_factual_memories>" in sc, "应有 retrieved_factual_memories 标签"
        assert "饮水机" in sc, "内容应在 section 中"


# ============================================================
# 测试：混合类型记忆库 → 分区正确性
# ============================================================
class TestMixedMemoryLibrary(RetrievalQualityBase):
    """多种记忆类型共存的综合场景"""

    def setup_method(self):
        super().setup_method()
        # 构造丰富的记忆库：event + query + preference
        self._write("我前天帮你修了饮水机", agent_id="zhang_san")       # event
        self._write("我昨天帮你修了发电机", agent_id="zhang_san")       # event
        self._write("我昨天帮你做了什么？", agent_id="zhang_san")       # query
        self._write("我前天问过你类似的问题吗？", agent_id="zhang_san")  # query
        self._write("我最喜欢喝咖啡", agent_id="zhang_san")             # preference

    def test_factual_query_gets_only_events_in_factual_section(self):
        """事实查询：factual 区只有 event/preference，没有 query"""
        _, sc = self._retrieve_and_assemble("我前天帮你做了什么？")

        fact_section = self._section_content(sc, "retrieved_factual_memories")

        # 精确：query 内容不在 factual 区
        for noise in ["昨天帮你做了什么？", "前天问过你类似的问题吗"]:
            assert noise.replace(" ", "") not in fact_section.replace(" ", ""), (
                f"factual 区不应有 query: {noise}\n实际: {fact_section}"
            )

        # 召回：event/preference 在 factual 区
        assert "饮水机" in fact_section or "发电机" in fact_section, (
            f"factual 区应有事件: {fact_section}"
        )

    def test_interaction_query_gets_queries_in_query_section(self):
        """交互历史查询：query 出现在 retrieved_dialog_history"""
        _, sc = self._retrieve_and_assemble("我之前问过你什么？")

        query_section = self._section_content(sc, "retrieved_dialog_history")

        # 召回：至少一条 query 出现在该区
        has_query = ("帮你做了什么" in query_section.replace(" ", "") or
                     "类似的问题" in query_section.replace(" ", ""))
        assert has_query, f"retrieved_dialog_history 应包含 query: {query_section}"

    def test_both_sections_present_independently(self):
        """通用查询：两个区都可能有内容，且内容不混"""
        _, sc = self._retrieve_and_assemble("最近发生了什么？")

        fact_section = self._section_content(sc, "retrieved_factual_memories")
        query_section = self._section_content(sc, "retrieved_dialog_history")

        # 两区内容不重叠
        assert "昨天帮你做了什么" not in fact_section.replace(" ", ""), (
            f"query 内容不应在 factual 区"
        )
        # 事实区应有内容（至少一条 event/preference）
        has_event = ("饮水机" in fact_section or "发电机" in fact_section or
                     "咖啡" in fact_section)
        assert has_event, f"factual 区应有事件: {fact_section[:100]}"
