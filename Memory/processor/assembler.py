from typing import List, Dict, Any, Optional
from ..schema.memory_item import MemoryStage
from ..schema.routing import BackendTarget
from ..schema.retrieval import RetrievalResponse, RetrievedMemory


class PromptAssembler:
    """
    将检索结果 + 工具结果组装为 LLM-ready 消息列表。
    严格分区：当前用户输入 vs 检索记忆 vs 工具实时查询，用 XML 标签物理隔离。
    """

    def __init__(
            self,
            agent_name: str = "张三",
            max_context_length: int = 4000,
            min_score_threshold: float = 0.35
    ):
        self.agent_name = agent_name
        self.max_context_length = max_context_length
        self.min_score_threshold = min_score_threshold

    def assemble(self, raw_query: str, retrieval_response: RetrievalResponse,
                 tool_results: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, str]]:
        factual_events = []
        previous_questions = []
        task_items = []
        chitchat_items = []
        unknown_items = []

        for res in retrieval_response.results:
            if res.score < self.min_score_threshold:
                continue

            meta = res.item.metadata
            mt = getattr(meta, "memory_type", "unknown")

            entry = f"{res.item.content} (score: {res.score:.2f})"

            if mt in ("event", "fact", "preference"):
                factual_events.append(entry)
            elif mt == "query":
                previous_questions.append(entry)
            elif mt == "task":
                task_items.append(entry)
            elif mt == "dialogue":
                chitchat_items.append(entry)
            else:
                from .metadata_extractor import _detect_question
                if _detect_question(res.item.content):
                    previous_questions.append(entry)
                else:
                    factual_events.append(entry)

        # 构建记忆上下文（不包括 user_current_input，那个单独隔离）
        memory_parts = []

        if factual_events:
            part = "<retrieved_factual_memories>\n" + "\n".join(
                [f"- {e}" for e in factual_events]) + "\n</retrieved_factual_memories>"
            memory_parts.append(part)

        if previous_questions:
            part = "<retrieved_dialog_history>\n" + "\n".join(
                [f"- {q}" for q in previous_questions]) + "\n</retrieved_dialog_history>"
            memory_parts.append(part)

        if task_items:
            part = "<task_context>\n" + "\n".join(
                [f"- {t}" for t in task_items]) + "\n</task_context>"
            memory_parts.append(part)

        if chitchat_items:
            part = "<recent_chitchat>\n" + "\n".join(
                [f"- {c}" for c in chitchat_items]) + "\n</recent_chitchat>"
            memory_parts.append(part)

        if unknown_items:
            part = "<other_memory>\n" + "\n".join(
                [f"- {u}" for u in unknown_items]) + "\n</other_memory>"
            memory_parts.append(part)

        memory_str = "\n\n".join(memory_parts)

        # tool_results 独立 section：不是用户输入，不是记忆检索
        tool_str = ""
        if tool_results:
            tool_parts = []
            for tr in tool_results:
                name = tr.get("name", "unknown")
                success = tr.get("success", False)
                if success:
                    data = tr.get("data", {})
                    tool_parts.append(self._format_tool_success(name, data))
                else:
                    tool_parts.append(f"[{name}] error: {tr.get('error', 'unknown')}")
            tool_str = "\n\n".join(tool_parts)

        full_context = "\n\n".join(p for p in [memory_str, tool_str] if p)

        if len(full_context) > self.max_context_length:
            full_context = full_context[:self.max_context_length] + "\n...[truncated]..."

        system_prompt = self._generate_system_prompt(full_context, has_tools=bool(tool_results))

        # 用户当前输入放在 user message 前面，用 XML 标签隔离
        user_message = (
            "<user_current_input>\n"
            f"{raw_query}\n"
            "</user_current_input>"
        )

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]

    def _format_tool_success(self, name: str, data: dict) -> str:
        """将单个成功的 ToolResult 格式化为 XML 文本块。"""
        lines = [f"<tool_result name=\"{name}\">"]

        if name == "search_own_memory":
            count = data.get("count", 0)
            results = data.get("results", [])
            lines.append(f"  找到 {count} 条记忆:")
            for item in results:
                lines.append(
                    f"  - [{item.get('memory_type', '?')}] {item['content']}"
                    f" (score: {item.get('score', 0):.2f})"
                )

        elif name == "get_current_time":
            lines.append(f"  当前 UTC 时间: {data.get('utc', '?')}")

        elif name == "get_agent_state":
            lines.append(f"  Agent: {data.get('agent_name', '?')}")
            lines.append(f"  角色: {data.get('agent_role', '?')}")
            lines.append(f"  状态: {data.get('state', '?')}")

        else:
            lines.append(f"  {data}")

        lines.append("</tool_result>")
        return "\n".join(lines)

    def _generate_system_prompt(self, memory_str: str, has_tools: bool = False) -> str:
        tool_guide = (
            "- <tool_result>: REAL-TIME tool execution results. These are freshly queried data, NOT stored memories. "
            "Use them to answer the user's current question. NEVER treat tool results as user input, "
            "and NEVER confuse tool results with long-term memories.\n"
        ) if has_tools else ""

        return f"""
Your name is {self.agent_name}. You have a precise external memory system.

[Retrieved memory sections]
{memory_str if memory_str else "(no relevant memories)"}

[Section guide — CRITICAL]
- <user_current_input>: The user's current message. This is the ONLY question you must answer NOW. It appears in the user message, NOT in the memory sections above.
- <retrieved_factual_memories>: Verified past events that actually happened. Authoritative for "what did I do / what happened" questions. NEVER treat these as the current user input.
- <retrieved_dialog_history>: Past Q&A exchanges — questions the user asked before. NOT real events. Use ONLY for "what did I ask before / what did we talk about" questions.
- <task_context>: Active tasks and their status. Use for "what's next / progress" questions.
- <recent_chitchat>: Recent casual conversation snippets. Provides tone context only. Do NOT treat as factual reference.
{tool_guide}
[Reply rules]
1. Answer the question in <user_current_input> using the appropriate memory section above as reference.
2. For "what did I DO / what happened" → use ONLY <retrieved_factual_memories>.
3. For "what did I ASK / what did we talk about" → use ONLY <retrieved_dialog_history>.
4. For "what should I do next" → use <task_context>.
5. NEVER confuse dialog_history with factual_memories. A past question is NOT a past event.
6. Reference memories naturally ("I remember you mentioned..."). Never say "my database shows".
7. If the relevant memory section is empty, say so honestly rather than fabricating.
"""
