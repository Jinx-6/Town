from typing import List, Dict, Any
from ..schema.memory_item import MemoryStage
from ..schema.routing import BackendTarget
from ..schema.retrieval import RetrievalResponse, RetrievedMemory


class PromptAssembler:
    """
    将检索结果组装为 LLM-ready 消息列表。
    严格分区：当前用户输入 vs 检索记忆，用 XML 标签物理隔离。
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

    def assemble(self, raw_query: str, retrieval_response: RetrievalResponse) -> List[Dict[str, str]]:
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

        if len(memory_str) > self.max_context_length:
            memory_str = memory_str[:self.max_context_length] + "\n...[truncated]..."

        system_prompt = self._generate_system_prompt(memory_str)

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

    def _generate_system_prompt(self, memory_str: str) -> str:
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

[Reply rules]
1. Answer the question in <user_current_input> using the appropriate memory section above as reference.
2. For "what did I DO / what happened" → use ONLY <retrieved_factual_memories>.
3. For "what did I ASK / what did we talk about" → use ONLY <retrieved_dialog_history>.
4. For "what should I do next" → use <task_context>.
5. NEVER confuse dialog_history with factual_memories. A past question is NOT a past event.
6. Reference memories naturally ("I remember you mentioned..."). Never say "my database shows".
7. If the relevant memory section is empty, say so honestly rather than fabricating.
"""
