"""
Shared test mocks — no external LLM / embedding dependencies.

Usage:
    from Test.mocks import MockLLMClient, MockVectorStore

    # Inject into memory processors
    mock_llm = MockLLMClient()
    planner = QueryPlanner(llm=mock_llm)

    # Inject into agents
    agent = MemoryAwareAgent(..., llm_client=mock_llm)
"""

import json
from typing import Optional, Dict, Any

# Default valid JSON that satisfies QueryPlanner.generate_plan() parser
_PLANNER_JSON = json.dumps({
    "original_query": "mock query",
    "instructions": [{
        "intent": "mock intent",
        "search_query": "mock search",
        "keywords": ["mock"],
        "time_filter": "all"
    }]
})


# ── Mock response helpers ──────────────────────────────

class _MockChoice:
    def __init__(self, content: str):
        self.message = _MockMessage(content)


class _MockMessage:
    def __init__(self, content: str):
        self.content = content
        self.tool_calls = None


class _MockCompletion:
    def __init__(self, content: str):
        self.choices = [_MockChoice(content)]


class _MockChat:
    def __init__(self, completions: "_MockCompletions"):
        self.completions = completions


class _MockCompletions:
    """Mock for openai.OpenAI().chat.completions.create() — sync."""

    def __init__(self, canned_response: str = _PLANNER_JSON):
        self.canned = canned_response
        self.calls: list = []

    def create(self, **kwargs) -> _MockCompletion:
        self.calls.append(kwargs)
        return _MockCompletion(self.canned)


class _MockAsyncCompletions:
    """Mock for openai.AsyncOpenAI().chat.completions.create() — async."""

    def __init__(self, canned_response: str = "(mock reply)"):
        self.canned = canned_response
        self.calls: list = []

    async def create(self, **kwargs) -> _MockCompletion:
        self.calls.append(kwargs)
        return _MockCompletion(self.canned)


# ── Mock OpenAI clients ────────────────────────────────

class MockSyncClient:
    """Drop-in replacement for openai.OpenAI, exposing .chat.completions.create()."""

    def __init__(self, canned_response: str = None):
        self.chat = _MockChat(_MockCompletions(canned_response or _PLANNER_JSON))

    @property
    def calls(self):
        return self.chat.completions.calls


class MockAsyncClient:
    """Drop-in replacement for openai.AsyncOpenAI, exposing .chat.completions.create()."""

    def __init__(self, canned_response: str = None):
        self.chat = _MockChat(_MockAsyncCompletions(canned_response or _PLANNER_JSON))

    @property
    def calls(self):
        return self.chat.completions.calls


# ── Mock LLMClient ─────────────────────────────────────

class MockLLMClient:
    """
    Unified mock for LLMClient, usable via both constructor injection
    and agent ``llm_client`` injection.

    Sync path (memory processors):
        >>> llm = MockLLMClient()
        >>> extractor = GraphExtractor(llm=llm)
        >>> # sync_client.chat.completions.create() returns valid QueryPlanner JSON

    Async path (agents):
        >>> llm = MockLLMClient(canned="task done")
        >>> agent = MemoryAwareAgent(..., llm_client=llm)
        >>> # llm.generate() returns "task done"
    """

    def __init__(self, canned_response: str = "(mock reply)"):
        self.model = "mock-model"
        self._canned = canned_response
        # Sync path returns valid JSON by default, async path uses canned_response
        self.sync_client = MockSyncClient(_PLANNER_JSON)
        self.async_client = MockAsyncClient(canned_response)

    # ── Agent-compatible async interface ───────────────

    async def generate(self, system_prompt: str, messages: list) -> str:
        return self._canned

    async def generate_with_tools(self, system_prompt, messages, tools) -> dict:
        return {"text": "", "tool_calls": [], "finish_reason": "stop"}

    # ── Call tracking ──────────────────────────────────

    @property
    def sync_calls(self) -> list:
        return self.sync_client.calls

    @property
    def async_calls(self) -> list:
        return self.async_client.calls


# ── Mock VectorStore ───────────────────────────────────

class MockVectorStore:
    """In-memory vector store mock — no ChromaDB / BGE dependencies."""

    def __init__(self):
        self._items: Dict[str, Any] = {}

    def add(self, item):
        self._items[item.id] = item

    def search(self, query: str, agent_id: str, limit: int = 5,
               metadata_filters: Optional[Dict[str, Any]] = None) -> list:
        from Memory.schema.retrieval import RetrievedMemory
        from Memory.schema.routing import BackendTarget

        results = []
        for item in self._items.values():
            if item.agent_id != agent_id:
                continue
            if metadata_filters:
                meta = item.metadata if isinstance(item.metadata, dict) else item.metadata.model_dump()
                if any(meta.get(k) != v for k, v in metadata_filters.items()):
                    continue
            results.append(RetrievedMemory(item=item, score=0.25, source=BackendTarget.VECTOR))
        return results[:limit]

    def delete_by_agent(self, agent_id: str):
        self._items = {k: v for k, v in self._items.items() if v.agent_id != agent_id}

    def delete(self, memory_id: str):
        self._items.pop(memory_id, None)

    def clear(self):
        self._items.clear()
