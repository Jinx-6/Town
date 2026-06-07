"""Integration tests: AgentRuntimeBundle + optional ConsolidateHub."""
import time
from agents.agent_factory import create_memory_aware_agent
from agents.agent_runtime import create_agent_runtime, AgentRuntimeBundle
from agents.memory_aware_agent import MemoryAwareAgent
from Memory.schema.memory_item import MemoryRole
from Memory.policies.retention import RetentionPolicy
from Test.mocks import MockLLMClient


class FakeSummarizer:
    """Returns canned summary without calling any LLM."""

    def __init__(self, canned: str = "compressed summary"):
        self._canned = canned
        self.call_count = 0

    def compress_to_semantic(self, dialogue_text: str) -> str:
        self.call_count += 1
        return self._canned


class NoopRetentionPolicy(RetentionPolicy):
    """Never compresses or deletes — safe for deterministic tests."""

    def should_compress(self, item, now=None):
        return False

    def should_delete(self, item, now=None):
        return False


def _wait_for_queue(dispatcher, timeout: float = 10.0) -> None:
    """Wait for the AsyncDispatcher background thread to drain its queue."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if dispatcher.event_queue.unfinished_tasks == 0:
            # Give a tiny extra cushion for the final task_done() + db commit
            time.sleep(0.1)
            return
        time.sleep(0.1)
    raise RuntimeError(f"Dispatcher queue did not drain within {timeout}s")


# Test 1

def test_create_agent_unchanged():
    """create_memory_aware_agent returns the original 4-tuple signature."""
    result = create_memory_aware_agent(
        agent_id="test_orig", agent_name="Test", agent_role="tester",
        llm_client=MockLLMClient(),
    )

    assert isinstance(result, tuple), f"Expected tuple, got {type(result)}"
    assert len(result) == 4, f"Expected 4 elements, got {len(result)}"

    agent, cache, sqlite, vector_store = result
    assert isinstance(agent, MemoryAwareAgent)
    assert cache is not None
    assert sqlite is not None
    assert vector_store is not None


# Test 2

def test_runtime_without_consolidation():
    """enable_consolidation=False: bundle has no consolidate_hub or index_manager."""
    bundle = create_agent_runtime(
        agent_id="test_off", agent_name="Test", agent_role="tester",
        llm_client=MockLLMClient(), enable_consolidation=False,
    )

    assert isinstance(bundle, AgentRuntimeBundle)
    assert isinstance(bundle.agent, MemoryAwareAgent)
    assert bundle.cache is not None
    assert bundle.sqlite is not None
    assert bundle.vector_store is not None
    assert bundle.index_manager is None
    assert bundle.consolidate_hub is None


# Test 3

def test_runtime_with_consolidation():
    """enable_consolidation=True: bundle holds both ConsolidateHub and IndexManager."""
    bundle = create_agent_runtime(
        agent_id="test_on", agent_name="Test", agent_role="tester",
        llm_client=MockLLMClient(), enable_consolidation=True,
        summarizer=FakeSummarizer(), retention_policy=NoopRetentionPolicy(),
    )

    assert bundle.consolidate_hub is not None, "ConsolidateHub should be created"
    assert bundle.index_manager is not None, "IndexManager should be created"

    # Verify dispatcher was backfilled with the same index_manager
    dispatcher = bundle.agent.ingest.dispatcher
    assert dispatcher.index_manager is bundle.index_manager

    # Verify consolidator was registered on the dispatcher
    assert dispatcher.consolidate_hub is bundle.consolidate_hub

    # Cron must NOT be running
    assert not bundle.consolidate_hub._running, "cron should not auto-start"


# Test 4

def test_consolidate_once_manual_trigger():
    """consolidate_once() runs synchronously without cron or LLM dependency."""
    bundle = create_agent_runtime(
        agent_id="test_manual", agent_name="Test", agent_role="tester",
        llm_client=MockLLMClient(), enable_consolidation=True,
        summarizer=FakeSummarizer(), retention_policy=NoopRetentionPolicy(),
    )

    # Inject a few memories via public ingest API
    bundle.agent.ingest.process_message(
        content="I repaired the server today",
        role=MemoryRole.USER,
        agent_id="test_manual",
    )
    bundle.agent.ingest.process_message(
        content="Got it, logged",
        role=MemoryRole.ASSISTANT,
        agent_id="test_manual",
    )
    bundle.agent.ingest.process_message(
        content="Thanks!",
        role=MemoryRole.USER,
        agent_id="test_manual",
    )

    # Wait for AsyncDispatcher background thread to flush writes to SQLite
    _wait_for_queue(bundle.agent.ingest.dispatcher)

    # Verify memories exist before consolidation
    recent_before = bundle.sqlite.list_recent(limit=20)
    assert len(recent_before) >= 3, f"Expected >=3 memories, got {len(recent_before)}"

    # Manual trigger
    result = bundle.consolidate_once()

    # Should return number of candidates submitted
    assert result >= 0, f"consolidate_once returned {result}"

    # Cron must still be off
    assert not bundle.consolidate_hub._running, "cron must never start"

    # Memories should still exist (NoopRetentionPolicy never deletes/compresses)
    recent_after = bundle.sqlite.list_recent(limit=20)
    assert len(recent_after) >= 3, f"Expected >=3 memories after, got {len(recent_after)}"


# Test 5

def test_consolidate_once_without_consolidation_raises():
    """Calling consolidate_once() when disabled raises RuntimeError."""
    bundle = create_agent_runtime(
        agent_id="test_err", agent_name="Test", agent_role="tester",
        llm_client=MockLLMClient(), enable_consolidation=False,
    )

    try:
        bundle.consolidate_once()
        assert False, "Should have raised RuntimeError"
    except RuntimeError as e:
        assert "not enabled" in str(e)
