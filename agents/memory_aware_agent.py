"""
MemoryAwareAgent — 记忆感知单智能体。

Phase 1: classify → retrieve → assemble → generate → ingest 单轮流程
Phase 2: 有限状态循环 run() — N 轮外部输入或自主 self-check turn
Phase 3: tool loop — 可选工具调用链（仅当 tool_registry 有 enabled 工具时激活）
"""
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Any, Optional, Callable

from Memory.schema.memory_item import MemoryRole
from Memory.schema.retrieval import RetrievalRequest, RetrievedMemory, RetrievalResponse


# ── 类型 ──────────────────────────────────────────────

class AgentState(str, Enum):
    IDLE = "idle"
    PERCEIVING = "perceiving"
    THINKING = "thinking"
    ACTING = "acting"
    ERROR = "error"


@dataclass
class AgentResponse:
    """一次 respond() 调用的完整结果。"""
    text: str
    intent_type: str
    is_autonomous: bool = False
    retrieved_memories: List[RetrievedMemory] = field(default_factory=list)
    pre_boost_snapshot: List[RetrievedMemory] = field(default_factory=list)
    context_messages: List[Dict[str, str]] = field(default_factory=list)
    rewritten_queries: List[str] = field(default_factory=list)
    tool_results: List[Dict[str, Any]] = field(default_factory=list)
    error: str = ""


@dataclass
class AgentRunResult:
    """run() 的汇总结果。"""
    turns: List[AgentResponse] = field(default_factory=list)
    final_state: AgentState = AgentState.IDLE
    total_elapsed_ms: int = 0
    errors: List[str] = field(default_factory=list)


# ── 自主 stimulus 模板 ─────────────────────────────────

_STIMULUS_TEMPLATES = [
    "回顾一下最近的对话，有什么重要信息？",
    "我刚才和用户的对话中，用户提到了什么关键事实？",
]


class DeterministicStimulus:
    def __init__(self, templates: Optional[List[str]] = None):
        self._templates = templates or _STIMULUS_TEMPLATES
        self._index = 0

    def next(self) -> str:
        t = self._templates[self._index % len(self._templates)]
        self._index += 1
        return t

    def reset(self):
        self._index = 0


# ── Agent ─────────────────────────────────────────────

class MemoryAwareAgent:
    """记忆感知智能体：单轮 respond + 有限状态循环 run + 可选 tool loop。"""

    def __init__(
        self,
        *,
        agent_id: str,
        agent_name: str,
        agent_role: str,
        ingest_hub,
        retrieve_hub,
        intent_classifier,
        prompt_assembler,
        llm_client,
        base_system_prompt: str = "",
        time_boost_fn=None,
        stimulus_strategy=None,
        tool_registry=None,
    ):
        self.agent_id = agent_id
        self.agent_name = agent_name
        self.agent_role = agent_role
        self.ingest = ingest_hub
        self.retrieve = retrieve_hub
        self.classifier = intent_classifier
        self.assembler = prompt_assembler
        self.llm = llm_client
        self.base_system_prompt = base_system_prompt or (
            f"你是{agent_name}，一位{agent_role}。"
            f"你在 Datawhale 办公室工作。请基于你的记忆与用户自然对话。"
            f"如果记忆中有用户之前告诉过你的信息，请自然地引用。"
        )
        self._time_boost = time_boost_fn
        self._stimulus = stimulus_strategy or DeterministicStimulus()
        self.tool_registry = tool_registry
        self.state = AgentState.IDLE

    # ── respond ────────────────────────────────────────

    async def respond(self, user_input: str,
                      is_autonomous: bool = False) -> AgentResponse:
        """
        处理单条输入。

        is_autonomous=True → stimulus 不存，response → memory_type="self_check"

        如果 self.tool_registry 有 enabled 工具：
          1. 先检索记忆 + 组装初始 prompt
          2. 运行 tool loop（LLM 决策 → dispatch → 收集 ToolResult）
          3. 用含 <tool_result> 的 prompt 做最终生成
        """
        error_msg = ""
        loop_results: List[Dict[str, Any]] = []
        final_messages = None

        try:
            # 1. 意图分类
            intent = self.classifier.classify(user_input)

            # 2. 检索
            if intent.intent_type == "chitchat":
                results = RetrievalResponse(
                    original_query=user_input, agent_id=self.agent_id
                )
                pre_boost = []
            else:
                req_kwargs: Dict[str, Any] = dict(
                    query=user_input, limit=5, agent_id=self.agent_id
                )
                if intent.intent_type == "qa_query":
                    req_kwargs["metadata_filters"] = {"is_factual_memory": True}
                    if intent.rewritten_queries:
                        req_kwargs["rewritten_queries"] = intent.rewritten_queries

                request = RetrievalRequest(**req_kwargs)
                results = self.retrieve.retrieve(request, agent_id=self.agent_id)

                pre_boost = [
                    RetrievedMemory(item=r.item, score=r.score, source=r.source)
                    for r in results.results
                ]
                if self._time_boost:
                    results.results = self._time_boost(user_input, results.results)

            # 3. 初始组装
            context_messages = self.assembler.assemble(user_input, results)

            # 4. tool loop（仅当有 enabled 工具时）
            if (self.tool_registry is not None
                    and len(self.tool_registry.list_enabled()) > 0):
                from agents.tool_loop import execute_tool_loop
                loop_result = await execute_tool_loop(
                    llm_client=self.llm,
                    registry=self.tool_registry,
                    initial_messages=context_messages,
                    system_prompt=self.base_system_prompt,
                )
                loop_results = loop_result.tool_results

            # 5. 最终生成（带 tool_results）—— 用纯 generate()，不用 generate_with_tools()
            final_messages = self.assembler.assemble(
                user_input, results, tool_results=loop_results or None
            )
            response_text = await self.llm.generate(
                system_prompt=self.base_system_prompt,
                messages=final_messages,
            )

        except Exception as e:
            error_msg = str(e)
            response_text = "(Agent 暂时无法回复...)"
            results = RetrievalResponse(
                original_query=user_input, agent_id=self.agent_id
            )
            pre_boost = []
            context_messages = []
            intent = self.classifier.classify(user_input)

        # 6. 写入记忆（retrieve-before-write）
        #    ToolResult 绝不进入 ingest 路径
        if is_autonomous:
            self._ingest_self_check(user_input, response_text)
        else:
            self.ingest.process_message(
                content=user_input,
                role=MemoryRole.USER,
                agent_id=self.agent_id,
                intent_type=intent.intent_type if hasattr(intent, 'intent_type') else None,
            )
            time.sleep(0.1)
            self.ingest.process_message(
                content=response_text,
                role=MemoryRole.ASSISTANT,
                agent_id=self.agent_id,
            )
            time.sleep(0.1)

        return AgentResponse(
            text=response_text,
            intent_type=intent.intent_type,
            is_autonomous=is_autonomous,
            retrieved_memories=results.results,
            pre_boost_snapshot=pre_boost,
            context_messages=final_messages or context_messages,
            rewritten_queries=intent.rewritten_queries,
            tool_results=loop_results,
            error=error_msg,
        )

    # ── run ────────────────────────────────────────────

    async def run(
        self,
        *,
        turns: int = 1,
        user_inputs: Optional[List[Optional[str]]] = None,
        on_state_change: Optional[Callable[[AgentState, AgentState, int], None]] = None,
    ) -> AgentRunResult:
        start_ts = int(time.time() * 1000)
        run_errors: List[str] = []
        responses: List[AgentResponse] = []

        self.state = AgentState.IDLE

        for i in range(turns):
            ui = None
            if user_inputs and i < len(user_inputs):
                ui = user_inputs[i]

            is_autonomous = ui is None
            stimulus = ui if ui is not None else self._stimulus.next()

            try:
                self._transition(AgentState.PERCEIVING, i, on_state_change)
                self._transition(AgentState.THINKING, i, on_state_change)

                resp = await self.respond(stimulus, is_autonomous=is_autonomous)

                self._transition(AgentState.ACTING, i, on_state_change)

                if resp.error:
                    self._transition(AgentState.ERROR, i, on_state_change)
                    run_errors.append(f"turn[{i}]: {resp.error}")
                else:
                    self._transition(AgentState.IDLE, i, on_state_change)

                responses.append(resp)

            except Exception as e:
                self.state = AgentState.ERROR
                run_errors.append(f"turn[{i}]: {e}")
                responses.append(AgentResponse(
                    text="", intent_type="unknown",
                    is_autonomous=is_autonomous, error=str(e),
                ))
                self._transition(AgentState.ERROR, i, on_state_change)

        self.state = AgentState.IDLE
        elapsed = int(time.time() * 1000) - start_ts

        return AgentRunResult(
            turns=responses,
            final_state=self.state,
            total_elapsed_ms=elapsed,
            errors=run_errors,
        )

    # ── helpers ────────────────────────────────────────

    def _transition(self, target: AgentState, turn: int, on_state_change):
        old = self.state
        self.state = target
        if on_state_change:
            on_state_change(old, target, turn)

    def _ingest_self_check(self, stimulus: str, response_text: str):
        self.ingest.process_message(
            content=response_text,
            role=MemoryRole.ASSISTANT,
            agent_id=self.agent_id,
            metadata={"memory_type": "self_check", "is_factual_memory": False},
        )
        time.sleep(0.05)

    def reset_stimulus(self):
        self._stimulus.reset()
