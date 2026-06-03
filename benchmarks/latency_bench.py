"""
Agent respond() end-to-end latency benchmark.

Uses MockLLMClient (fixed 50ms delay) to exclude LLM noise.
Measures classify → retrieve → assemble → mock_generate → ingest.

Scenarios: chitchat / fact_recall / qa_with_tools
Output: p50 / p95 / p99 (p99 only if --runs >= 100)

Usage:
  python benchmarks/latency_bench.py --runs 30
"""
import sys
import os
import time
import argparse
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from Memory.storage.working_cache import WorkingMemoryCache
from Memory.storage.sqlite_log import SQLiteLogStorage
from Memory.processor.planner import RetrievalPlan, RetrievalInstruction
from Memory.processor.assembler import PromptAssembler
from Memory.processor.intent_classifier import create_intent_classifier
from Memory.policies.scoring import MemoryScorer
from Memory.policies.retrieval_policy import RetrievalPolicy
from Memory.policies.update_policy import UpdatePolicy
from Memory.hub.ingest import IngestHub
from Memory.hub.retrieve import RetrieveHub
from Memory.hub.async_dispatcher import AsyncDispatcher
from Memory.processor.router import WriteRouter
from Memory.schema.routing import BackendTarget
from Memory.schema.memory_item import MemoryRole, MemoryItem, MemoryStage, MemoryMetadata
from Memory.schema.retrieval import RetrievalRequest, RetrievedMemory
from agents.memory_aware_agent import MemoryAwareAgent
from agents.event import Event

AGENT_ID = "bench_latency"
_DB_PATH = "bench_latency.db"


class MockVectorStore:
    def __init__(self):
        self._items = {}
    def add(self, item):
        self._items[item.id] = item
    def search(self, query, limit=5, agent_id="", metadata_filters=None):
        return []
    def delete_by_agent(self, agent_id):
        self._items = {k: v for k, v in self._items.items()
                       if v.agent_id != agent_id}


class DelayFakeLLM:
    """Fixed 50ms delay, returns canned responses."""
    def __init__(self, delay_ms=50):
        self._delay = delay_ms
    async def generate(self, system_prompt, messages):
        await _async_sleep(self._delay / 1000.0)
        return "这是一条测试回复。"
    async def generate_with_tools(self, system_prompt, messages, tools):
        await _async_sleep(self._delay / 1000.0)
        return {"text": "使用了工具查询", "tool_calls": [], "finish_reason": "stop"}


async def _async_sleep(seconds):
    import asyncio
    await asyncio.sleep(seconds)


def _build_agent(use_tools=False):
    if os.path.exists(_DB_PATH):
        os.remove(_DB_PATH)

    cache = WorkingMemoryCache()
    sqlite = SQLiteLogStorage(db_path=_DB_PATH)
    vector_store = MockVectorStore()

    backends = {BackendTarget.SQLITE: sqlite, BackendTarget.VECTOR: vector_store}
    dispatcher = AsyncDispatcher(
        backends=backends, extractor=None, conflict_resolver=None,
        index_manager=None, update_policy=UpdatePolicy(),
    )
    ingest = IngestHub(working_cache=cache, router=WriteRouter(),
                       dispatcher=dispatcher)

    planner = MagicMock()
    planner.generate_plan.return_value = RetrievalPlan(
        original_query="test", instructions=[
            RetrievalInstruction(intent="search", search_query="test", keywords=[])
        ])

    scorer = MemoryScorer()
    retrieval_policy = RetrievalPolicy()
    retrieve = RetrieveHub(
        planner=planner, cache=cache, sqlite=sqlite,
        vector_store=vector_store, graph_store=None,
        scorer=scorer, policy=retrieval_policy,
    )

    classifier = create_intent_classifier(use_llm=False)
    assembler = PromptAssembler(agent_name="bench_agent")

    agent = MemoryAwareAgent(
        agent_id=AGENT_ID, agent_name="bench_agent", agent_role="测试",
        ingest_hub=ingest, retrieve_hub=retrieve,
        intent_classifier=classifier, prompt_assembler=assembler,
        llm_client=DelayFakeLLM(delay_ms=50),
        base_system_prompt="测试用系统提示",
    )

    if use_tools:
        from agents.tool import create_builtin_tools
        from agents.tool_registry import ToolRegistry
        tools = create_builtin_tools(
            retrieve_hub=retrieve, agent_id=AGENT_ID,
            agent_name="bench_agent", agent_role="测试",
            get_state_fn=lambda: agent.state,
        )
        reg = ToolRegistry()
        reg.register_all(tools)
        agent.tool_registry = reg

    return agent


def _p(arr, k):
    """k-th percentile (0-100)."""
    if not arr:
        return 0
    s = sorted(arr)
    idx = int(len(s) * k / 100.0)
    return s[min(idx, len(s) - 1)]


def run():
    import asyncio

    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=30)
    args = parser.parse_args()

    scenarios = {
        "chitchat": "今天天气真不错啊",
        "fact_recall": "你还记得我昨天帮你修了什么吗",
        "qa_with_tools": "现在几点了？帮我查一下时间",
    }

    for name, _input in scenarios.items():
        agent = _build_agent(use_tools=(name == "qa_with_tools"))
        lats = []
        for _ in range(args.runs):
            t0 = time.perf_counter()
            asyncio.run(agent.respond(_input))
            lats.append((time.perf_counter() - t0) * 1000)

        print(f"\n--- {name} (runs={args.runs}) ---")
        print(f"  input: {_input}")
        print(f"  p50 = {_p(lats, 50):.1f} ms")
        print(f"  p95 = {_p(lats, 95):.1f} ms")
        if args.runs >= 100:
            print(f"  p99 = {_p(lats, 99):.1f} ms")
        print(f"  min = {min(lats):.1f} ms  max = {max(lats):.1f} ms")
        print(f"  avg = {sum(lats)/len(lats):.1f} ms")

    os.remove(_DB_PATH) if os.path.exists(_DB_PATH) else None


if __name__ == "__main__":
    run()
