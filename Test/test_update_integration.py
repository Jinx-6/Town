"""Integration tests: AgentRuntimeBundle + optional UpdateHub."""
import time
from agents.agent_runtime import create_agent_runtime, AgentRuntimeBundle
from agents.memory_aware_agent import MemoryAwareAgent
from Memory.schema.memory_item import MemoryRole
from Test.mocks import MockLLMClient


def _wait_for_queue(dispatcher, timeout: float = 10.0) -> None:
    """Wait for the AsyncDispatcher background thread to drain its queue."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if dispatcher.event_queue.unfinished_tasks == 0:
            time.sleep(0.1)
            return
        time.sleep(0.1)
    raise RuntimeError(f"Dispatcher queue did not drain within {timeout}s")


# Test 1

def test_runtime_with_update_hub():
    """enable_update_hub=True: bundle holds UpdateHub and shared IndexManager."""
    bundle = create_agent_runtime(
        agent_id="test_up_on", agent_name="Test", agent_role="tester",
        llm_client=MockLLMClient(), enable_update_hub=True,
    )

    assert isinstance(bundle, AgentRuntimeBundle)
    assert bundle.update_hub is not None, "UpdateHub should be created"
    assert bundle.index_manager is not None, "IndexManager should be created"

    # Verify dispatcher was backfilled with the same index_manager
    dispatcher = bundle.agent.ingest.dispatcher
    assert dispatcher.index_manager is bundle.index_manager

    # ConsolidateHub should NOT be created
    assert bundle.consolidate_hub is None


# Test 2

def test_update_content_replaces_memory():
    """update_memory() wipes old content and writes new content across stores."""
    bundle = create_agent_runtime(
        agent_id="test_up_c", agent_name="Test", agent_role="tester",
        llm_client=MockLLMClient(), enable_update_hub=True,
    )

    # Inject original memory
    bundle.agent.ingest.process_message(
        content="I work at Tencent",
        role=MemoryRole.USER,
        agent_id="test_up_c",
    )
    _wait_for_queue(bundle.agent.ingest.dispatcher)

    # Find the injected memory by content search
    old_hits = bundle.sqlite.search_with_metadata(
        query="Tencent", agent_id="test_up_c", limit=10,
    )
    assert len(old_hits) >= 1, "Expected at least 1 memory about Tencent"
    memory_id = old_hits[0].id
    assert old_hits[0].content == "I work at Tencent"

    # Update: user corrects their statement
    result = bundle.update_memory(memory_id, "I already left Tencent")
    assert result is True

    # Wait for async write to complete
    _wait_for_queue(bundle.agent.ingest.dispatcher)

    # After update: old content must be gone, new content must exist
    updated = bundle.sqlite.get_by_id(memory_id)
    assert updated is not None, f"Memory {memory_id} should still exist"
    assert updated.content == "I already left Tencent", \
        f"Expected new content, got: {updated.content}"

    # Verify old content is no longer in SQLite
    all_recent = bundle.sqlite.list_recent(limit=50)
    old_contents = {r.content for r in all_recent}
    assert "I work at Tencent" not in old_contents, \
        "Old content should be gone after update"


# Test 3

def test_update_metadata_modifies_metadata():
    """update_memory_metadata() changes metadata fields without touching content."""
    bundle = create_agent_runtime(
        agent_id="test_up_m", agent_name="Test", agent_role="tester",
        llm_client=MockLLMClient(), enable_update_hub=True,
    )

    # Inject a memory
    bundle.agent.ingest.process_message(
        content="The weather is nice today",
        role=MemoryRole.USER,
        agent_id="test_up_m",
    )
    _wait_for_queue(bundle.agent.ingest.dispatcher)

    hits = bundle.sqlite.search_with_metadata(
        query="weather", agent_id="test_up_m", limit=10,
    )
    assert len(hits) >= 1
    memory_id = hits[0].id

    # Update metadata
    result = bundle.update_memory_metadata(memory_id, importance=8)
    assert result is True

    # Verify metadata changed, content unchanged
    updated = bundle.sqlite.get_by_id(memory_id)
    assert updated is not None
    assert updated.content == "The weather is nice today"
    metadata_dict = updated.metadata.model_dump() if updated.metadata else {}
    assert metadata_dict.get("importance") == 8, \
        f"Expected importance=8, got {metadata_dict.get('importance')}"


# Test 4

def test_update_memory_without_enabling_raises():
    """Calling update_memory() when disabled raises RuntimeError."""
    bundle = create_agent_runtime(
        agent_id="test_up_err", agent_name="Test", agent_role="tester",
        llm_client=MockLLMClient(), enable_update_hub=False,
    )

    try:
        bundle.update_memory("any_id", "new content")
        assert False, "Should have raised RuntimeError"
    except RuntimeError as e:
        assert "not enabled" in str(e)
