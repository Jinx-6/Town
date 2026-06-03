"""
Pub/Sub throughput benchmark.

Creates 2 / 5 / 10 AgentWorkers (with FakeAgents), measures:
- EventBus dispatch throughput (events/sec)
- AgentWorker processing throughput (events/sec)
Reports separately to distinguish routing cost from agent cost.

Usage:
  python benchmarks/throughput_bench.py
"""
import sys
import time
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.event import Event
from agents.event_bus import EventBus, SchedulerPolicy
from agents.agent_worker import AgentWorker
from simulator import Simulator


class _FakeAgent:
    """Minimal agent for throughput testing — no LLM, no memory."""
    def __init__(self, agent_id, agent_name, delay_ms=0):
        self.agent_id = agent_id
        self.agent_name = agent_name
        self._delay = delay_ms

    async def respond(self, content):
        if self._delay:
            await asyncio.sleep(self._delay / 1000.0)
        from agents.memory_aware_agent import AgentResponse
        return AgentResponse(
            text=f"[{self.agent_name}] 回复了",
            intent_type="chitchat",
        )


def run_bench(n_agents: int, max_events: int = 200):
    bus = EventBus(policy=SchedulerPolicy(max_total_events=max_events))
    sim = Simulator(bus=bus)

    for i in range(n_agents):
        agent = _FakeAgent(f"agent_{i}", f"Agent-{i}")
        sim.add_agent(AgentWorker(agent), topics=["town.public"])

    sim.inject_topic("town.public", "benchmark seed message", source="system")

    t0 = time.perf_counter()
    dispatched = asyncio.run(sim.bus.run_until_idle(max_events))
    elapsed_ms = (time.perf_counter() - t0) * 1000

    bus_rate = dispatched / (elapsed_ms / 1000) if elapsed_ms > 0 else 0

    total_processed = sum(w.processed_count for w in sim._workers.values())
    worker_rate = total_processed / (elapsed_ms / 1000) if elapsed_ms > 0 else 0

    print(f"\n--- {n_agents} agents ---")
    print(f"  total events dispatched: {dispatched}")
    print(f"  total agent processes:  {total_processed}")
    print(f"  elapsed:                {elapsed_ms:.1f} ms")
    print(f"  EventBus throughput:    {bus_rate:.1f} events/sec")
    print(f"  AgentWorker throughput: {worker_rate:.1f} events/sec")

    return bus_rate, worker_rate, dispatched, elapsed_ms


def run():
    print("=== throughput_bench ===")
    print("  note: FakeAgent (no LLM, no memory stores)")

    for n in [2, 5, 10]:
        run_bench(n)


if __name__ == "__main__":
    run()
