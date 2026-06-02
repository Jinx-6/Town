"""
多智能体发布订阅对话演示 (CLI)

用法：
  python demo_multi_agent.py                           # 真实 LLM
  python demo_multi_agent.py --mock --turns 10         # Dry-run 测试
  python demo_multi_agent.py --reset --debug --turns 15
"""
import sys
import os
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from agents.agent_factory import create_memory_aware_agent, reset_memory
from agents.agent_worker import AgentWorker
from agents.event_bus import SchedulerPolicy
from simulator import Simulator
from LLMClient import LLMClient


class MultiAgentFakeLLM:
    def __init__(self, agent_name: str):
        self.agent_name = agent_name
        self.call_count = 0
        self.calls = []

    async def generate(self, system_prompt: str, messages: list) -> str:
        self.call_count += 1
        user_input = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_input = msg.get("content", "")
                break
        self.calls.append(user_input)
        return (
            f"[{self.agent_name} 模拟回复 #{self.call_count}] "
            f"收到了「{user_input[:50]}」"
        )

    async def generate_with_tools(self, system_prompt, messages, tools):
        return {"text": "", "tool_calls": [], "finish_reason": "stop"}


def _parse_args():
    topic = "town.public"
    turns = 10
    do_reset = False
    do_debug = False
    do_mock = False
    seed = "大家好，今天天气真不错！"

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--topic" and i + 1 < len(args):
            i += 1; topic = args[i]
        elif a == "--turns" and i + 1 < len(args):
            i += 1; turns = int(args[i])
        elif a == "--seed" and i + 1 < len(args):
            i += 1; seed = args[i]
        elif a == "--reset":
            do_reset = True
        elif a == "--debug":
            do_debug = True
        elif a == "--mock":
            do_mock = True
        i += 1
    return topic, turns, do_reset, do_debug, do_mock, seed


def _agent_system_prompt(name: str, role: str, topic: str) -> str:
    return (
        f"你是{name}，一位{role}。你在赛博小镇生活和工作。"
        f"你会通过「{topic}」频道看到其他居民的消息。"
        f"请基于你的记忆，自然地回应他们的发言，就像在日常对话中一样。"
        f"回复要简短自然，控制在 2-3 句话以内。"
        f"如果记忆中有相关的过去信息，可以引用。"
    )


async def run_demo():
    topic, turns, do_reset, do_debug, do_mock, seed = _parse_args()

    if do_debug:
        print(f"[System] topic={topic}  turns={turns}  seed={seed}")
        if do_mock:
            print("[System] mock 模式 — 无需 Ollama")

    agent_cfgs = [
        {"id": "zhang_san", "name": "张三", "role": "Python工程师"},
        {"id": "li_si",   "name": "李四", "role": "产品经理"},
    ]

    if do_reset:
        for cfg in agent_cfgs:
            db_path = f"demo_{cfg['id']}.db"
            if os.path.exists(db_path):
                os.remove(db_path)
            try:
                from Memory.storage.vector_store import VectorStore
                vs = VectorStore()
                vs.delete_by_agent(cfg["id"])
            except Exception:
                pass
        print("[System] 所有 Agent 记忆已重置")

    workers = []
    for cfg in agent_cfgs:
        llm = MultiAgentFakeLLM(cfg["name"]) if do_mock else LLMClient()
        agent, cache, sqlite, vector = create_memory_aware_agent(
            agent_id=cfg["id"],
            agent_name=cfg["name"],
            agent_role=cfg["role"],
            llm_client=llm,
            base_system_prompt=_agent_system_prompt(
                cfg["name"], cfg["role"], topic
            ),
        )
        workers.append(AgentWorker(agent))

    sim = Simulator(policy=SchedulerPolicy(max_total_events=turns))

    for w in workers:
        sim.add_agent(w, topics=[topic])

    print(f"\n{'=' * 60}")
    print(f"  赛博小镇 — 多智能体发布订阅对话演示")
    print(f"{'=' * 60}")
    print(f"  频道: {topic}")
    print(f"  参与者: 张三, 李四")
    print(f"  最大消息数: {turns}")
    if do_mock:
        print(f"  模式: mock (dry-run)")
    print(f"{'=' * 60}\n")

    sim.inject_topic(topic, seed, source="system")

    if do_debug:
        print(f"[System] 注入种子消息:")
        print(f"  「{seed}」")
        print(f"  source=system  topic={topic}\n")

        original_dispatch = sim.bus.dispatch_next

        async def debug_dispatch():
            if sim.bus.total_dispatched >= sim.bus._policy.max_total_events:
                return 0
            if not sim.bus._queue:
                return 0
            evt = sim.bus._queue[0]
            print(f"  ── dispatch #{sim.bus.total_dispatched + 1} ──")
            print(f"  event_id={evt.event_id}  source={evt.source_agent_id}")
            print(f"  type={evt.type}  content={evt.content[:80]}...")
            result = await original_dispatch()
            if result == 0:
                print(f"  (无匹配订阅者)")
            else:
                print(f"  → {result} 个订阅者")
            print()
            return result

        sim.bus.dispatch_next = debug_dispatch

    dispatched = await sim.run_async(max_events=turns)

    print(f"\n{'=' * 60}")
    print(f"  对话结束")
    print(f"{'=' * 60}")

    for agent_id, count in sim.summary().items():
        name = next(
            (c["name"] for c in agent_cfgs if c["id"] == agent_id), agent_id
        )
        print(f"  {name} ({agent_id}): 处理了 {count} 条消息")

    print(f"  总计 dispatch: {dispatched} 个事件")
    print()


if __name__ == "__main__":
    asyncio.run(run_demo())
