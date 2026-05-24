from typing import List, Dict, Any
from ..schema.memory_item import MemoryStage
from ..schema.routing import BackendTarget
from ..schema.retrieval import RetrievalResponse, RetrievedMemory


class PromptAssembler:
    """
    Intelligent context assembly engine.
    Sorts retrieved memories into typed sections instead of a single blob.
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
        # 1. Typed buckets
        factual_events = []
        previous_questions = []
        task_items = []
        dialogue_items = []
        unknown_items = []

        # 2. Classify by metadata
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
                dialogue_items.append(entry)
            else:
                from .metadata_extractor import _detect_question
                if _detect_question(res.item.content):
                    previous_questions.append(entry)
                else:
                    factual_events.append(entry)

        # 3. Build XML sections
        context_parts = []

        if factual_events:
            part = "<factual_event_memory>\n" + "\n".join(
                [f"- {e}" for e in factual_events]) + "\n</factual_event_memory>"
            context_parts.append(part)

        if previous_questions:
            part = "<previous_user_questions>\n" + "\n".join(
                [f"- {q}" for q in previous_questions]) + "\n</previous_user_questions>"
            context_parts.append(part)

        if task_items:
            part = "<task_state>\n" + "\n".join(
                [f"- {t}" for t in task_items]) + "\n</task_state>"
            context_parts.append(part)

        if dialogue_items:
            part = "<recent_dialogue>\n" + "\n".join(
                [f"- {d}" for d in dialogue_items]) + "\n</recent_dialogue>"
            context_parts.append(part)

        if unknown_items:
            part = "<other_memory>\n" + "\n".join(
                [f"- {u}" for u in unknown_items]) + "\n</other_memory>"
            context_parts.append(part)

        context_str = "\n\n".join(context_parts)

        # 4. Truncate (keep factual events first)
        if len(context_str) > self.max_context_length:
            context_str = context_str[:self.max_context_length] + "\n...[truncated]..."

        # 5. Build system prompt
        system_prompt = self._generate_system_prompt(context_str)

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": raw_query}
        ]

    def _generate_system_prompt(self, context_str: str) -> str:
        return f"""
Your name is {self.agent_name}. You have a precise external memory system.

[Memory sections]
{context_str if context_str else "(no relevant memories)"}

[Section guide]
- <factual_event_memory>: Real events that happened. Use as authoritative source for "what happened / what did I do" questions.
- <previous_user_questions>: Questions the user asked before. Reflects user intent and dialog history, NOT real events. Use for "what did I ask before" questions.
- <task_state>: Task progress and todos.
- <recent_dialogue>: Recent conversation snippets.

[Reply rules]
1. For factual questions, use only <factual_event_memory>. Never treat <previous_user_questions> as real events.
2. Reference memories naturally (e.g. "I remember you mentioned..."). Never say "my database shows".
3. When asked "what did I ask before", use <previous_user_questions>. When asked "what did I do", use only <factual_event_memory>.
4. If all sections are empty, answer from common sense. Don't make things up.
"""
