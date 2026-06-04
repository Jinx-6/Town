"""
Multi-agent collaboration demo — LLM-driven task orchestration.

Usage:
  python demo_collab.py                    # real LLM (Ollama required)
  python demo_collab.py --mock             # dry-run with FakeLLM
  python demo_collab.py --mock --verbose   # show all events
"""
import sys
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from agents.event_bus import EventBus, SchedulerPolicy
from agents.agent_factory import create_memory_aware_agent
from agents.collab_worker import CollabAgentWorker
from agents.task_coordinator import TaskCoordinator
from agents.collab_schema import AgentCapability
from LLMClient import LLMClient

AGENTS = [
    {"id": "zhang_san", "name": "张三", "role": "Python工程师"},
    {"id": "li_si", "name": "李四", "role": "产品经理"},
    {"id": "wang_wu", "name": "王五", "role": "UI设计师"},
]

COLLAB_TOPIC = "town.collab"


class FakeAgentLLM:
    """Canned agent responses for --mock mode."""

    def __init__(self, agent_name: str, agent_role: str):
        self.agent_name = agent_name
        self.agent_role = agent_role

    async def generate(self, system_prompt, messages):
        return (
            f"[{self.agent_name} / {self.agent_role}] "
            f"已完成任务。输出了详细的方案内容。"
        )

    async def generate_with_tools(self, system_prompt, messages, tools):
        return {"text": "", "tool_calls": [], "finish_reason": "stop"}


class FakeCollabLLM:
    """Returns canned decomposition JSON + synthesis text for --mock mode."""

    async def generate(self, system_prompt, messages):
        if "任务分解" in system_prompt:
            return """
{
  "subtasks": [
    {"title": "撰写需求文档", "description": "针对用户登录页面撰写包含功能需求、验收标准的产品需求文档", "required_role": "产品经理"},
    {"title": "评估技术方案", "description": "评估登录功能的技术实现方案，包括认证方式选型和开发工时估算", "required_role": "Python工程师"},
    {"title": "设计UI布局", "description": "设计登录页面的UI布局方案，包括桌面端和移动端适配建议", "required_role": "UI设计师"}
  ]
}"""
        return (
            "【协作方案汇总】\n\n"
            "1. 需求概述：用户登录页面应支持邮箱+密码、手机验证码两种方式\n"
            "2. 技术方案：采用JWT认证，前端React + 后端FastAPI，预计3人日\n"
            "3. 设计建议：简洁风格，移动端优先，参考Apple登录页布局\n"
            "4. 下一步：产品经理输出PRD → 设计师出高保真 → 工程师开发"
        )

    async def generate_with_tools(self, system_prompt, messages, tools):
        return {"text": "", "tool_calls": [], "finish_reason": "stop"}


async def run_demo(use_mock: bool = False, verbose: bool = False):
    print("\n" + "=" * 60)
    print("  赛博小镇 — 多 Agent LLM 协作演示")
    mode = "mock (dry-run)" if use_mock else "real LLM"
    print(f"  模式: {mode}")
    print("=" * 60)

    # ── Build agents ──────────────────────────────────────
    collab_llm = FakeCollabLLM() if use_mock else LLMClient()

    workers = []
    agents_map = {}
    for cfg in AGENTS:
        agent_llm = FakeAgentLLM(cfg["name"], cfg["role"]) if use_mock else LLMClient()
        agent, _c, _s, _v = create_memory_aware_agent(
            agent_id=cfg["id"], agent_name=cfg["name"], agent_role=cfg["role"],
            llm_client=agent_llm,
            base_system_prompt=(
                f"你是{cfg['name']}，一位{cfg['role']}。你在赛博小镇工作。"
                f"你会收到协作任务，请基于你的专业知识完成任务并输出结果。"
                f"回复要专业、具体，2-4 句话。"
            ),
        )
        worker = CollabAgentWorker(agent)
        workers.append(worker)
        agents_map[cfg["id"]] = AgentCapability(
            agent_id=cfg["id"], role=cfg["role"],
        )

    # ── Build runtime ─────────────────────────────────────
    bus = EventBus(policy=SchedulerPolicy(max_total_events=100))
    for w in workers:
        bus.subscribe(COLLAB_TOPIC, w.handle_event)

    coordinator = TaskCoordinator(
        bus, collab_topic=COLLAB_TOPIC, llm_client=collab_llm,
    )

    # ── Collaboration request ─────────────────────────────
    request = "帮我做新功能「用户登录页面」的完整方案"
    roles = [a["role"] for a in AGENTS]

    print(f"\n[请求] {request}\n")

    # 1. Decompose
    print("[Coordinator] 正在分解任务...")
    tasks = await coordinator.decompose_with_llm(request, roles, parent_task_id="demo")
    print(f"  → 分解为 {len(tasks)} 个子任务:")
    for t in tasks:
        print(f"    [{t.required_role}] {t.title}")

    # 2. Assign
    print("\n[Coordinator] 正在分配任务...")
    for t in tasks:
        ok = coordinator.assign_by_role(t, agents_map)
        status = "OK" if ok else "FAIL"
        print(f"  {status} {t.title} → {t.assigned_to}")

    # 3. Run
    print(f"\n[EventBus] 运行中...")
    dispatched = await bus.run_until_idle(max_events=100)
    print(f"  共分发 {dispatched} 个事件")

    if verbose:
        print(f"\n[事件历史]")
        for evt in bus.history:
            print(f"  [{evt.type}] {evt.source_agent_id} → {evt.target_agent_id}: "
                  f"{evt.content[:60]}...")

    # 4. Collect + Synthesize
    print(f"\n[Coordinator] 正在汇总结果...")
    results = coordinator.collect_results("demo")
    print(f"  收集到 {len(results)} 个结果")

    summary = await coordinator.synthesize_results("demo", request)
    print(f"\n{'=' * 60}")
    print(f"  最终方案")
    print(f"{'=' * 60}")
    print(summary)
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock", action="store_true", help="Use FakeLLM (no Ollama)")
    parser.add_argument("--verbose", action="store_true", help="Print all events")
    args = parser.parse_args()

    asyncio.run(run_demo(use_mock=args.mock, verbose=args.verbose))
