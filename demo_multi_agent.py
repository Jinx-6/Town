"""
多智能体发布订阅对话演示 (CLI)

用法:
  python demo_multi_agent.py                                    # 默认 pubsub
  python demo_multi_agent.py --scenario pubsub                  # 发布订阅对话
  python demo_multi_agent.py --scenario memory_isolation        # 记忆隔离
  python demo_multi_agent.py --scenario temporal_memory         # 时间记忆召回
  python demo_multi_agent.py --scenario skill_observation       # 技能观察
  python demo_multi_agent.py --scenario tool_calling            # 工具调用
  python demo_multi_agent.py --mock --turns 5                   # Dry-run
  python demo_multi_agent.py --reset --debug
"""
import sys
import os
import asyncio
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from agents.agent_factory import create_memory_aware_agent, reset_memory
from agents.agent_worker import AgentWorker
from agents.event_bus import SchedulerPolicy
from agents.event import Event
from agents.tool import create_builtin_tools
from agents.tool_registry import ToolRegistry
from simulator import Simulator
from LLMClient import LLMClient


class MultiAgentFakeLLM:
    def __init__(self, agent_name: str, canned: str = ""):
        self.agent_name = agent_name
        self.call_count = 0
        self.calls = []
        self._canned = canned

    async def generate(self, system_prompt: str, messages: list) -> str:
        self.call_count += 1
        user_input = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_input = msg.get("content", "")
                break
        self.calls.append(user_input)
        if self._canned:
            return self._canned.format(name=self.agent_name, input=user_input[:40])
        return (
            f"[{self.agent_name} 模拟回复 #{self.call_count}] "
            f"收到了「{user_input[:50]}」"
        )

    async def generate_with_tools(self, system_prompt, messages, tools):
        return {"text": "", "tool_calls": [], "finish_reason": "stop"}


class FakeSentimentLLM:
    """Returns a parseable sentiment score for RelationshipManager in mock/demo mode."""

    def __init__(self, score: str = "2"):
        self._score = score
        self.call_count = 0

    async def generate(self, system_prompt: str, messages: list) -> str:
        self.call_count += 1
        return self._score

    async def generate_with_tools(self, system_prompt, messages, tools):
        return {"text": "", "tool_calls": [], "finish_reason": "stop"}


# ── scenario configs ────────────────────────────────────

SCENARIOS = {}


def _register(name, **kwargs):
    SCENARIOS[name] = kwargs


_register("pubsub",
    title="发布订阅多 Agent 对话",
    agents=[
        {"id": "zhang_san", "name": "张三", "role": "Python工程师"},
        {"id": "li_si",   "name": "李四", "role": "产品经理"},
    ],
    topic="town.public",
    seeds=["大家好，今天天气真不错！"],
    turns=10,
)

_register("memory_isolation",
    title="记忆隔离 — 定向消息不泄漏",
    agents=[
        {"id": "zhang_san", "name": "张三", "role": "Python工程师"},
        {"id": "li_si",   "name": "李四", "role": "产品经理"},
    ],
    topic="town.public",
    seeds=["今天是周一，所有人注意。"],
    directed=[
        {"target": "zhang_san", "content": "张三，你的密码是 8888，不要告诉别人"},
        {"target": "li_si",   "content": "李四，你的密码是 9999，不要告诉别人"},
    ],
    turns=8,
)

_register("temporal_memory",
    title="时间记忆召回 — 昨天/前天",
    agents=[
        {"id": "demo_agent", "name": "张三", "role": "Python工程师"},
    ],
    topic="town.public",
    seeds=[
        "我昨天帮你修了发电机",
        "我前天帮你修了饮水机",
    ],
    query="你还记得我昨天帮你做了什么吗？",
    turns=8,
)

_register("skill_observation",
    title="技能观察 — ObservePublicEventSkill",
    agents=[
        {"id": "zhang_san", "name": "张三", "role": "Python工程师"},
        {"id": "li_si",   "name": "李四", "role": "产品经理"},
    ],
    topic="town.public",
    seeds=["今天下午 3 点开会讨论项目进度", "好的，我准备好 PPT 了"],
    use_observe_skill=True,
    turns=6,
)

_register("tool_calling",
    title="工具调用 — search_own_memory / get_current_time / get_agent_state",
    agents=[
        {"id": "tool_agent", "name": "张三", "role": "Python工程师"},
    ],
    topic="town.public",
    seeds=["我叫进喜，我是你的同事", "我今天帮你修了路由器"],
    query="帮我查一下当前时间，再看看你记不记得我是谁",
    use_tools=True,
    turns=6,
)


# ── arg parsing ─────────────────────────────────────────

def _parse_args():
    scenario = ""
    topic = ""
    turns = 0
    do_reset = False
    do_debug = False
    do_mock = False
    seed = ""

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--scenario" and i + 1 < len(args):
            i += 1; scenario = args[i]
        elif a == "--topic" and i + 1 < len(args):
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
    return scenario, topic, turns, do_reset, do_debug, do_mock, seed


def _resolve_scenario(args_scenario, args_topic, args_turns, args_seed):
    name = args_scenario or "pubsub"
    if name not in SCENARIOS:
        print(f"[System] 未知 scenario '{name}'，可用: {', '.join(SCENARIOS)}")
        sys.exit(1)
    cfg = SCENARIOS[name].copy()
    if args_topic:
        cfg["topic"] = args_topic
    if args_turns:
        cfg["turns"] = args_turns
    if args_seed:
        cfg["seeds"] = [args_seed]
    return name, cfg


def _agent_system_prompt(name: str, role: str, topic: str,
                         has_tools: bool = False) -> str:
    prompt = (
        f"你是{name}，一位{role}。你在赛博小镇生活和工作。"
        f"你会通过「{topic}」频道看到消息。"
        f"请基于你的记忆自然地回应，回复要简短，2-3 句话以内。"
    )
    if has_tools:
        prompt += (
            f"你可以使用 get_current_time 查看时间，"
            f"使用 search_own_memory 检索你的记忆，"
            f"使用 get_agent_state 查看自己的状态。"
        )
    return prompt


# ── main ────────────────────────────────────────────────

async def run_demo():
    args = _parse_args()
    name, cfg = _resolve_scenario(args[0], args[1], args[2], args[6])
    _, _, turns, do_reset, do_debug, do_mock, _ = args

    topic = cfg["topic"]
    turns = cfg["turns"]
    agent_cfgs = cfg["agents"]
    seeds = cfg.get("seeds", [])
    directed = cfg.get("directed", [])
    query = cfg.get("query", "")
    use_observe = cfg.get("use_observe_skill", False)
    use_tools = cfg.get("use_tools", False)

    if do_debug:
        print(f"[System] scenario={name}  topic={topic}  turns={turns}")
        if do_mock:
            print("[System] mock 模式 — 无需 Ollama")

    # Reset
    if do_reset:
        for c in agent_cfgs:
            db_path = f"demo_{c['id']}.db"
            if os.path.exists(db_path):
                os.remove(db_path)
            try:
                from Memory.storage.vector_store import VectorStore
                vs = VectorStore()
                vs.delete_by_agent(c["id"])
            except Exception:
                pass
        print("[System] 所有 Agent 记忆已重置")

    # Build agents
    workers = []
    agent_objs = []
    for c in agent_cfgs:
        llm = MultiAgentFakeLLM(c["name"]) if do_mock else LLMClient()
        agent, cache, sqlite, vector = create_memory_aware_agent(
            agent_id=c["id"],
            agent_name=c["name"],
            agent_role=c["role"],
            llm_client=llm,
            base_system_prompt=_agent_system_prompt(
                c["name"], c["role"], topic, has_tools=use_tools
            ),
        )
        # Wire tools if requested
        if use_tools:
            tools = create_builtin_tools(
                retrieve_hub=agent.retrieve,
                agent_id=agent.agent_id,
                agent_name=agent.agent_name,
                agent_role=agent.agent_role,
                get_state_fn=lambda a=agent: a.state,
            )
            registry = ToolRegistry()
            registry.register_all(tools)
            agent.tool_registry = registry
        agent_objs.append(agent)
        workers.append(AgentWorker(agent))

    # Build simulator with optional RelationshipManager
    from relationship import RelationshipManager
    rel_llm = FakeSentimentLLM(score="2") if do_mock else LLMClient()
    rel_manager = RelationshipManager(llm_client=rel_llm)

    sim = Simulator(
        policy=SchedulerPolicy(max_total_events=turns),
        relationship_manager=rel_manager,
    )
    for w in workers:
        sim.add_agent(w, topics=[topic])

    # Wire observe skill if requested
    observe_results = []
    if use_observe:
        from skills.builtin import ObservePublicEventSkill
        from skills.registry import SkillRegistry as SR
        skill = ObservePublicEventSkill()
        skill_reg = SR()
        skill_reg.register(skill)

    # ── header ──
    print(f"\n{'=' * 60}")
    print(f"  赛博小镇 — {cfg['title']}")
    print(f"{'=' * 60}")
    print(f"  频道: {topic}")
    names = ", ".join(c["name"] for c in agent_cfgs)
    print(f"  参与者: {names}")
    print(f"  最大消息数: {turns}")
    if do_mock:
        print(f"  模式: mock (dry-run)")
    if use_observe:
        print(f"  Skill: ObservePublicEvent")
    if use_tools:
        print(f"  Tool: 3 个内置工具")
    print(f"{'=' * 60}\n")

    # Inject seed messages
    for seed in seeds:
        sim.inject_topic(topic, seed, source="system")
        if do_debug:
            print(f"[seed] 「{seed}」")

    # Inject directed messages
    for d in directed:
        sim.bus.publish(Event(
            source_agent_id="system", topic=topic,
            content=d["content"], target_agent_id=d["target"],
            type="system",
        ))
        if do_debug:
            print(f"[directed → {d['target']}] 「{d['content'][:50]}」")

    # Inject final query if scenario has one
    if query:
        sim.inject_topic(topic, query, source="system")
        if do_debug:
            print(f"[query] 「{query}」")

    # ── debug wrapper ──
    if do_debug:
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

    # ── run ──
    dispatched = await sim.run_async(max_events=turns)

    # ── optional skill pass ──
    if use_observe:
        print(f"\n  [ObservePublicEventSkill] 扫描已处理事件...")
        for evt in sim.bus.history:
            if skill.applies_to(evt):
                for a in agent_objs:
                    result = await skill.run(evt, a)
                    if result.success:
                        observe_results.append(
                            f"  {a.agent_name} 观察到: {evt.source_agent_id} 说了"
                            f"「{evt.content[:40]}」"
                        )
        for line in observe_results:
            print(line)

    # ── summary ──
    print(f"\n{'=' * 60}")
    print(f"  运行结束")
    print(f"{'=' * 60}")

    for agent_id, count in sim.summary().items():
        name = next(
            (c["name"] for c in agent_cfgs if c["id"] == agent_id), agent_id
        )
        print(f"  {name} ({agent_id}): 处理了 {count} 条消息")

    print(f"  总计 dispatch: {dispatched} 个事件")

    # Memory isolation check
    if name == "memory_isolation":
        print(f"\n  [隔离检查]")
        time.sleep(1.0)
        for a in agent_objs:
            recent = a.retrieve.sqlite.search_with_metadata(
                query="密码", agent_id=a.agent_id, limit=10)
            contents = [r.content for r in recent]
            has_own = any("密码" in c for c in contents)
            print(f"  {a.agent_name} 的记忆中有密码相关信息: {has_own}")
            for c in contents:
                print(f"    - {c[:80]}")

    # Temporal memory check
    if name == "temporal_memory":
        print(f"\n  [时间记忆召回]")
        time.sleep(1.0)
        a = agent_objs[0]
        recent = a.retrieve.sqlite.search_with_metadata(
            query="修了", agent_id=a.agent_id, limit=20)
        for r in recent:
            print(f"  - [{r.timestamp}] {r.content[:80]}")

    print()


if __name__ == "__main__":
    asyncio.run(run_demo())
