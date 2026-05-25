"""
时间记忆混淆问题 —— 稳定复现脚本
====================================
场景:
  1. 重置演示记忆
  2. 插入「昨天 + 发电机」记忆 (24h 前)
  3. 插入「前天 + 买水」记忆   (48h 前)
  4. 查询「你还记得我昨天帮我做了什么吗」
  5. 查询「你还记得我前天帮我做了什么吗」
  6. 打印完整检索追踪日志

用法:
  python demo_reproduce.py
"""
import sys
import os
import time
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from Memory.schema.memory_item import MemoryItem, MemoryRole, MemoryStage, MemoryMetadata
from Memory.schema.routing import BackendTarget
from Memory.schema.retrieval import RetrievalRequest, RetrievedMemory

from Memory.storage.working_cache import WorkingMemoryCache
from Memory.storage.sqlite_log import SQLiteLogStorage
from Memory.storage.vector_store import VectorStore

from Memory.processor.router import WriteRouter
from Memory.processor.planner import QueryPlanner
from Memory.policies.scoring import MemoryScorer
from Memory.policies.retrieval_policy import RetrievalPolicy
from Memory.policies.update_policy import UpdatePolicy

from Memory.hub.async_dispatcher import AsyncDispatcher
from Memory.hub.ingest import IngestHub
from Memory.hub.retrieve import RetrieveHub

# 导入 demo_cli 中的调试和时间关键词工具
from demo_cli import (
    apply_time_boost, reset_memory, setup_memory,
    print_debug_retrieval, _detect_time_keywords,
)

AGENT_ID = "demo_agent"


def make_item(content: str, hours_ago: float, item_id: str) -> MemoryItem:
    """创建一条带有指定时间戳的记忆"""
    ts = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return MemoryItem(
        id=item_id,
        content=content,
        role=MemoryRole.USER,
        agent_id=AGENT_ID,
        timestamp=ts,
        stage=MemoryStage.SEMANTIC,
        metadata=MemoryMetadata(importance=5),
    )


def _print_sep(title: str):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


async def main():
    print("=" * 60)
    print("  时间记忆混淆 — 稳定复现")
    print("=" * 60)

    # ── Step 0: 初始化 & 重置 ────────────────────────────────
    _print_sep("Step 0: 初始化记忆引擎并重置")
    ingest, retrieve, cache, sqlite, vector_store = setup_memory(AGENT_ID)
    reset_memory(AGENT_ID, sqlite, vector_store)
    ingest, retrieve, cache, sqlite, vector_store = setup_memory(AGENT_ID)
    print("[OK] 记忆库已清空")

    # ── Step 1: 注入测试记忆 ─────────────────────────────────
    _print_sep("Step 1: 注入两条测试记忆")

    item_yesterday = make_item(
        content="我昨天帮你修好了镇上的发电机",
        hours_ago=24.0,
        item_id="repro_yesterday",
    )
    item_day_before = make_item(
        content="我前天帮你买了一瓶水",
        hours_ago=48.0,
        item_id="repro_day_before",
    )

    # 写入 SQLite + Vector
    sqlite.add(item_yesterday)
    sqlite.add(item_day_before)
    vector_store.add(item_yesterday)
    vector_store.add(item_day_before)
    time.sleep(0.3)

    print(f"  [写入] id=repro_yesterday  | {item_yesterday.timestamp.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"         内容: {item_yesterday.content}")
    print(f"  [写入] id=repro_day_before | {item_day_before.timestamp.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"         内容: {item_day_before.content}")

    # ── Step 2: 查询「昨天」─────────────────────────────────
    _print_sep("Step 2: 查询「你还记得我昨天帮我做了什么吗？」")

    query_yesterday = "你还记得我昨天帮我做了什么吗？"
    req_y = RetrievalRequest(query=query_yesterday, limit=5, agent_id=AGENT_ID)
    results_y = retrieve.retrieve(req_y, agent_id=AGENT_ID)
    pre_boost_y = [RetrievedMemory(item=r.item, score=r.score, source=r.source)
                   for r in results_y.results]
    results_y.results = apply_time_boost(query_yesterday, results_y.results)

    print_debug_retrieval(
        query=query_yesterday,
        results_pre_boost=pre_boost_y,
        results_post_boost=results_y.results,
    )

    # ── Step 3: 查询「前天」─────────────────────────────────
    _print_sep("Step 3: 查询「你还记得我前天帮我做了什么吗？」")

    query_day_before = "你还记得我前天帮我做了什么吗？"
    req_d = RetrievalRequest(query=query_day_before, limit=5, agent_id=AGENT_ID)
    results_d = retrieve.retrieve(req_d, agent_id=AGENT_ID)
    pre_boost_d = [RetrievedMemory(item=r.item, score=r.score, source=r.source)
                   for r in results_d.results]
    results_d.results = apply_time_boost(query_day_before, results_d.results)

    print_debug_retrieval(
        query=query_day_before,
        results_pre_boost=pre_boost_d,
        results_post_boost=results_d.results,
    )

    # ── Step 4: 判断结果 ─────────────────────────────────────
    _print_sep("Step 4: 结果判断")

    def check(query: str, results, expected_word: str, expected_label: str):
        if results:
            top_content = results[0].item.content
            is_correct = expected_word in top_content
            status = "[PASS]" if is_correct else "[FAIL]"
            print(f"  {status}  查询「{query}」-> 第一条: {top_content[:60]}")
            print(f"           期望包含: 「{expected_word}」({expected_label})")
            return is_correct
        else:
            print(f"  [FAIL]  查询「{query}」-> 无结果")
            return False

    ok1 = check(query_yesterday, results_y.results, "发电机", "昨天记忆")
    ok2 = check(query_day_before, results_d.results, "一瓶水", "前天记忆")

    print(f"\n{'─' * 60}")
    if ok1 and ok2:
        print(f"  全部通过 [PASS]")
    else:
        print(f"  存在失败 [FAIL] (这正是时间记忆混淆的症状)")
    print(f"{'─' * 60}")

    # 清理
    reset_memory(AGENT_ID, sqlite, vector_store)


if __name__ == "__main__":
    asyncio.run(main())
