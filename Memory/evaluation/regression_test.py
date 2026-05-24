# @Time    :2026/5/13 09:02
# @Author  :进喜
# @File    :regression_test.py
# @Software:PyCharm


import sys
from pathlib import Path

import pytest
from typing import Set

pytest.importorskip("neo4j", reason="Neo4j driver not installed")

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from Memory.storage.graph_db import GraphStore
from Memory.policies.update_policy import UpdatePolicy, UpdateAction
from Memory.evaluation.metrics import RetrievalMetrics, GenerationMetrics


# 模拟大模型客户端 (为了测试时不花钱，这里可以造个假的回声筒)
class MockLLMClient:
    def generate(self, prompt: str) -> str:
        # 简单模拟：假设 AI 总是是个老实人
        return '{"is_faithful": true, "hallucinated_facts": [], "score": 1.0}'


@pytest.fixture
def test_env():
    """
    测试环境准备 (Fixture)
    每次跑测试前，准备一个干净的数据库和策略引擎。
    """
    # 注意：真实测试时，请连测试库，别连生产库把你真实数据删了！
    graph_store = GraphStore(uri="bolt://localhost:7687", user="neo4j", password="password")

    # 清理测试用户的旧数据 (Cypher 语句)
    with graph_store.driver.session() as session:
        session.run("MATCH (n:Entity) DETACH DELETE n")

    policy = UpdatePolicy()

    yield graph_store, policy

    # 测试结束，清理现场
    graph_store.close()


# ==========================================
# 🧪 测试用例 1：测试图谱的“覆写 (OVERWRITE)”策略
# 场景：用户从北京搬到了上海
# ==========================================
def test_memory_overwrite_policy(test_env):
    graph_store, policy = test_env
    subject = "用户"
    rel = "居住在"

    # 1. 注入旧记忆：住在北京
    graph_store.add_relation(subject, rel, "北京", memory_id="mem_001")

    # 2. 系统侦测到新记忆：住在上海
    new_triplet = {"start": subject, "rel": rel, "end": "上海"}
    old_objects = graph_store.get_objects_for_predicate(subject, rel)

    # 3. 模拟 Dispatcher 触发冲突裁决
    for old_obj in old_objects:
        action = policy.resolve_conflict(
            existing_edge={"end": old_obj},
            new_edge=new_triplet,
            new_confidence=0.9
        )

        # 验证：策略必须判定为 OVERWRITE
        assert action == UpdateAction.OVERWRITE, "策略错误：'居住在' 应该触发 OVERWRITE"

        # 模拟执行覆写
        if action == UpdateAction.OVERWRITE:
            graph_store.delete_relation(subject, rel, old_obj)
            graph_store.add_relation(subject, rel, new_triplet["end"], memory_id="mem_002")

    # 4. 验证数据库最终状态：只有上海，没有北京
    final_objects = graph_store.get_objects_for_predicate(subject, rel)
    assert "上海" in final_objects, "新记忆落盘失败"
    assert "北京" not in final_objects, "旧记忆清理失败，出现了精神分裂！"


# ==========================================
# 🧪 测试用例 2：测试“存档 (ARCHIVE)”与“记忆泄漏”
# 场景：跳槽后，AI 不能把前公司当成现公司
# ==========================================
def test_memory_archive_and_leak(test_env):
    graph_store, policy = test_env
    subject = "用户"
    rel = "就职于"

    # 1. 初始化旧状态
    graph_store.add_relation(subject, rel, "腾讯", memory_id="mem_101")

    # 2. 模拟触发更新：去了字节跳动
    new_triplet = {"start": subject, "rel": rel, "end": "字节跳动"}
    old_objects = graph_store.get_objects_for_predicate(subject, rel)

    # 3. 执行策略
    for old_obj in old_objects:
        action = policy.resolve_conflict({"end": old_obj}, new_triplet, 0.95)

        assert action == UpdateAction.ARCHIVE, "策略错误：'就职于' 应该触发 ARCHIVE"

        if action == UpdateAction.ARCHIVE:
            graph_store.delete_relation(subject, rel, old_obj)
            graph_store.add_relation(subject, f"曾{rel}", old_obj, memory_id="mem_102")
            graph_store.add_relation(subject, rel, new_triplet["end"], memory_id="mem_102")

    # 4. 验证图谱状态
    current_companies = graph_store.get_objects_for_predicate(subject, "就职于")
    past_companies = graph_store.get_objects_for_predicate(subject, "曾就职于")

    assert "字节跳动" in current_companies
    assert "腾讯" in past_companies

    # 5. ✨ 联动 metrics.py 进行泄漏测试
    metrics = GenerationMetrics(llm_client=MockLLMClient())
    # 模拟大模型生成了这样一句话
    ai_answer = "我知道你现在在字节跳动工作，适应得怎么样？"

    # 检查 AI 有没有把归档的“腾讯”当成现任泄露出来
    has_leak = metrics.evaluate_forgetting_leak(ai_answer, archived_memories=past_companies)
    assert not has_leak, "糟糕！AI 提到了不该提的旧事实（记忆泄漏）！"


# ==========================================
# 🧪 测试用例 3：测试检索的命中率指标 (Hit Rate)
# 场景：确保微观指标能正常计算
# ==========================================
def test_retrieval_hit_rate():
    # 模拟 RetrieveHub 返回的记忆 ID 列表
    mock_retrieved_ids = ["mem_001", "mem_005", "mem_008"]

    # 我们预期的黄金记忆 ID
    expected_ids: Set[str] = {"mem_001", "mem_008"}

    # 调用 metrics.py 进行数学计算
    hit_rate = RetrievalMetrics.calculate_hit_rate(mock_retrieved_ids, expected_ids)

    # 3 个里命中了 2 个，预期是 1.0 (因为期望的 2 个都在返回的 3 个里)
    assert hit_rate == 1.0, f"命中率计算错误，预期 1.0，实际 {hit_rate}"

    mrr = RetrievalMetrics.calculate_mrr(mock_retrieved_ids, expected_ids)
    # 期望的 mem_001 在第一个位置，所以 MRR 是 1/1 = 1.0
    assert mrr == 1.0, f"MRR 计算错误，实际 {mrr}"