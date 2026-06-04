"""
Retrieval quality benchmark.

Injects 20 seed memories across 4 memory_types and 5 time buckets,
then runs 10 queries (5 temporal + 5 semantic).
Metrics: Precision@3, Recall@3, MRR@3, Temporal Precision@1, avg latency.

Usage:
  python benchmarks/retrieval_bench.py
"""
import sys
import os
import time
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import MagicMock

from Memory.storage.working_cache import WorkingMemoryCache
from Memory.storage.sqlite_log import SQLiteLogStorage
from Memory.processor.planner import RetrievalPlan, RetrievalInstruction
from Memory.policies.scoring import MemoryScorer
from Memory.policies.retrieval_policy import RetrievalPolicy
from Memory.hub.retrieve import RetrieveHub
from Memory.schema.routing import BackendTarget
from Memory.schema.memory_item import MemoryRole, MemoryItem, MemoryStage, MemoryMetadata
from Memory.schema.retrieval import RetrievalRequest, RetrievedMemory

AGENT_ID = "bench_retrieval"
_DB_PATH = "bench_retrieval.db"

# ── Mock VectorStore (in-memory, no embedding) ──────────

class MockVectorStore:
    def __init__(self):
        self._items = {}

    def add(self, item):
        self._items[item.id] = item

    def search(self, query, limit=5, agent_id="", metadata_filters=None):
        results = []
        for item in self._items.values():
            if agent_id and item.agent_id != agent_id:
                continue
            score = 0.85
            if metadata_filters:
                mt = getattr(item.metadata, "memory_type", "")
                if "is_factual_memory" in metadata_filters:
                    if metadata_filters["is_factual_memory"] and mt == "query":
                        score = 0.25
            results.append(RetrievedMemory(item=item, score=score,
                                          source=BackendTarget.VECTOR))
        return sorted(results, key=lambda r: r.score, reverse=True)[:limit]

    def delete_by_agent(self, agent_id):
        self._items = {k: v for k, v in self._items.items()
                       if v.agent_id != agent_id}


# ── seed data ───────────────────────────────────────────

# 20 memories: fact(7) / event(7) / task(3) / dialogue(3)
# Time buckets: today / yesterday / 2d / 3d / last_week
SEEDS = [
    # today
    ("刚刚重启了服务器 server01", "fact", "today", "repair"),
    ("CPU 温度正常 服务器状态良好", "fact", "today", "observation"),
    ("紧急 bug 需要立刻修复登录模块", "task", "today", "task_create"),
    ("你刚才问服务器是否又挂了", "dialogue", "today", "question"),
    # yesterday
    ("昨天帮你修了发电机 generator", "fact", "yesterday", "repair"),
    ("昨天下午数据库备份完成 backup", "fact", "yesterday", "backup"),
    ("大楼昨天停电了三个小时", "event", "yesterday", "incident"),
    ("昨天讨论了新版本上线时间", "dialogue", "yesterday", "discussion"),
    # 2 days ago
    ("前天买了新的饮水机 water", "fact", "2_days_ago", "purchase"),
    ("前天下午召开了全员会议", "event", "2_days_ago", "meeting"),
    ("前天提交了 Q2 预算报告 budget", "task", "2_days_ago", "report"),
    ("前天说需要升级 Python 版本", "dialogue", "2_days_ago", "discussion"),
    # 3 days ago
    ("三天前服务器硬盘更换完毕", "event", "3_days_ago", "hardware"),
    ("三天前收到了新显示器快递", "fact", "3_days_ago", "delivery"),
    ("三天前调整了防火墙规则", "task", "3_days_ago", "config"),
    ("三天前办公室空调坏了", "event", "3_days_ago", "incident"),
    # last week
    ("上周完成了年终代码审查 review", "event", "last_week", "review"),
    ("上周买了五盆绿植放在办公室", "fact", "last_week", "purchase"),
    ("上周培训了新入职的同事", "event", "last_week", "training"),
    ("上周更新了项目 Roadmap 文档", "fact", "last_week", "doc"),
]

# 10 queries: (query_text_for_metrics_label, actual_search_terms, relevant_indices, is_temporal)
QUERIES = [
    # temporal queries (use date keywords in queries)
    ("昨天帮我做了什么", "昨天 修 发电机", [4, 5, 6], True),
    ("前天发生了什么事", "前天 会议 饮水机", [8, 9, 10], True),
    ("最近服务器有什么问题", "服务器 停电 bug", [0, 3, 6], True),
    ("三天前做了什么改动", "三天前 服务器 防火墙", [13, 15], True),
    ("上周完成了什么", "上周 代码审查 文档 培训", [17, 18, 19], True),
    # semantic queries (use topical keywords)
    ("关于服务器的记忆", "服务器 server", [0, 1, 3, 6, 13], False),
    ("买了什么东西", "买了 快递 饮水机", [8, 13, 17], False),
    ("最近开了什么会议", "会议", [9], False),
    ("办公室的变化", "办公室 绿植 空调", [14, 17], False),
    ("紧急和修复的事情", "紧急 bug 修复", [2, 4], False),
]


def setup_pipeline(use_real: bool = False):
    if os.path.exists(_DB_PATH):
        os.remove(_DB_PATH)
    cache = WorkingMemoryCache()
    sqlite = SQLiteLogStorage(db_path=_DB_PATH)
    if use_real:
        from Memory.storage.vector_store import VectorStore
        vector_store = VectorStore()
    else:
        vector_store = MockVectorStore()
    planner = MagicMock()
    def _mock_plan(query):
        return RetrievalPlan(original_query=query, instructions=[
            RetrievalInstruction(intent="search", search_query=query, keywords=[])
        ])
    planner.generate_plan.side_effect = _mock_plan
    scorer = MemoryScorer()
    retrieval_policy = RetrievalPolicy()
    retrieve = RetrieveHub(
        planner=planner, cache=cache, sqlite=sqlite,
        vector_store=vector_store, graph_store=None,
        scorer=scorer, policy=retrieval_policy,
    )
    return sqlite, vector_store, retrieve


def inject_seeds(sqlite, vector_store):
    for content, mem_type, rel_time, evt_type in SEEDS:
        meta = MemoryMetadata(
            memory_type=mem_type,
            relative_time=rel_time,
            event_type=evt_type,
            is_factual_memory=(mem_type not in ("dialogue", "query")),
        )
        item = MemoryItem(
            content=content, role=MemoryRole.USER,
            agent_id=AGENT_ID, stage=MemoryStage.EPISODIC,
            metadata=meta,
        )
        sqlite.add(item)
        vector_store.add(item)


def ap_at_k(retrieved, relevant, k=3):
    """Average Precision at K."""
    hits = 0
    s = 0.0
    for i in range(min(k, len(retrieved))):
        if i in relevant:
            hits += 1
            s += hits / (i + 1)
    return s / min(len(relevant), k) if relevant else 0.0


def recall_at_k(retrieved, relevant, k=3):
    top_k = set(retrieved[:k])
    rel = set(relevant)
    return len(top_k & rel) / len(rel) if rel else 0.0


def mrr(retrieved, relevant):
    for i, r in enumerate(retrieved):
        if r in relevant:
            return 1.0 / (i + 1)
    return 0.0


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", action="store_true",
                        help="Use real ChromaDB + BGE-small-zh (requires sentence-transformers)")
    args = parser.parse_args()

    mode = "ChromaDB + BGE-small-zh (real embedding)" if args.real else "MockVectorStore (keyword-only, no embedding)"
    print(f"=== retrieval_bench ===")
    print(f"  agent_id={AGENT_ID}")
    print(f"  mode: {mode}")

    sqlite, vector_store, retrieve = setup_pipeline(use_real=args.real)
    inject_seeds(sqlite, vector_store)

    p_at_3 = []
    r_at_3 = []
    mrr_scores = []
    tp_at_1 = []  # temporal precision at 1
    latencies = []

    for q_label, search_terms, relevant_indices, is_temporal in QUERIES:
        req = RetrievalRequest(query=search_terms, limit=10, agent_id=AGENT_ID)
        t0 = time.perf_counter()
        resp = retrieve.retrieve(req, agent_id=AGENT_ID)
        elapsed = (time.perf_counter() - t0) * 1000
        latencies.append(elapsed)

        retrieved_indices = []
        for r in resp.results:
            content = r.item.content
            for idx, (c, *_) in enumerate(SEEDS):
                if c == content:
                    retrieved_indices.append(idx)
                    break

        p = ap_at_k(retrieved_indices, relevant_indices, k=3)
        r = recall_at_k(retrieved_indices, relevant_indices, k=3)
        m = mrr(retrieved_indices, relevant_indices)
        p_at_3.append(p)
        r_at_3.append(r)
        mrr_scores.append(m)

        if is_temporal and retrieved_indices:
            tp_at_1.append(1.0 if retrieved_indices[0] in relevant_indices else 0.0)

        print(f"  Q: {q_label[:50]}...")
        print(f"     Prec@3={p:.3f}  Rec@3={r:.3f}  MRR={m:.3f}  "
              f"latency={elapsed:.1f}ms  hits={len(resp.results)}")

    print(f"\n--- summary ---")
    print(f"  Precision@3       = {sum(p_at_3)/len(p_at_3):.3f}")
    print(f"  Recall@3          = {sum(r_at_3)/len(r_at_3):.3f}")
    print(f"  MRR@3             = {sum(mrr_scores)/len(mrr_scores):.3f}")
    print(f"  Temporal Prec@1   = {sum(tp_at_1)/len(tp_at_1):.3f}" if tp_at_1 else "  Temporal Prec@1   = N/A")
    latencies.sort()
    p50 = latencies[len(latencies)//2]
    p95 = latencies[int(len(latencies)*0.95)]
    print(f"  avg latency       = {sum(latencies)/len(latencies):.1f} ms")
    print(f"  p50 latency       = {p50:.1f} ms")
    print(f"  p95 latency       = {p95:.1f} ms")

    if args.real:
        print(f"  note: latency includes BGE-small-zh embedding computation")
        print(f"  mock reference: P@3=0.07  R@3=0.22  MRR@3=0.40  (keyword-only SQLite LIKE)")
    else:
        print(f"  note: latency excludes embedding (MockVectorStore)")
        print(f"  tip: run with --real for ChromaDB + BGE-small-zh semantic retrieval numbers")

    # cleanup
    os.remove(_DB_PATH) if os.path.exists(_DB_PATH) else None
    if args.real:
        vector_store.delete_by_agent(AGENT_ID)


if __name__ == "__main__":
    run()
