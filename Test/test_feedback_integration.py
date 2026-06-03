"""Integration tests: FeedbackCollector + UpdateHub correction bridge."""
import os
import time
import tempfile
import json

from agents.agent_runtime import create_agent_runtime, AgentRuntimeBundle
from agents.memory_aware_agent import MemoryAwareAgent
from Memory.evaluation.online_feedback import (
    FeedbackCollector, FeedbackEvent, FeedbackType,
)
from Memory.schema.memory_item import MemoryRole


class FakeLLM:
    """Canned-response LLM for tests — no external dependencies."""

    def __init__(self, reply: str = "test"):
        self._reply = reply

    async def generate(self, system_prompt: str, messages: list) -> str:
        return self._reply

    async def generate_with_tools(self, system_prompt, messages, tools):
        return {"text": "", "tool_calls": [], "finish_reason": "stop"}


def _wait_for_queue(dispatcher, timeout: float = 10.0) -> None:
    """Wait for the AsyncDispatcher background thread to drain its queue."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if dispatcher.event_queue.unfinished_tasks == 0:
            time.sleep(0.1)
            return
        time.sleep(0.1)
    raise RuntimeError(f"Dispatcher queue did not drain within {timeout}s")


def _make_feedback_event(**overrides) -> FeedbackEvent:
    kwargs = {
        "event_id": "evt_test",
        "message_id": "msg_test",
        "feedback_type": FeedbackType.THUMBS_UP,
    }
    kwargs.update(overrides)
    return FeedbackEvent(**kwargs)


# Test 1

def test_runtime_with_feedback_collector():
    """enable_feedback_collector=True: bundle holds a FeedbackCollector."""
    bundle = create_agent_runtime(
        agent_id="test_fb_on", agent_name="Test", agent_role="tester",
        llm_client=FakeLLM(), enable_feedback_collector=True,
    )

    assert bundle.feedback_collector is not None, "FeedbackCollector should be created"


# Test 2

def test_record_feedback_writes_jsonl():
    """record_feedback() writes a JSONL record to the log file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        fc = FeedbackCollector(log_dir=tmpdir)
        bundle = create_agent_runtime(
            agent_id="test_fb_log", agent_name="Test", agent_role="tester",
            llm_client=FakeLLM(),
            enable_feedback_collector=True,
            feedback_collector=fc,
        )

        event = _make_feedback_event(
            event_id="fbk_001",
            feedback_type=FeedbackType.THUMBS_UP,
            cited_memory_ids=["mem_a", "mem_b"],
        )
        result = bundle.record_feedback(event)

        assert result["recorded"] is True

        log_file = os.path.join(tmpdir, "online_feedback.jsonl")
        assert os.path.exists(log_file), f"Expected log file at {log_file}"

        with open(log_file, "r", encoding="utf-8") as f:
            content = f.read()

        assert "fbk_001" in content
        assert "thumbs_up" in content
        assert "mem_a" in content
        assert "mem_b" in content


# Test 3

def test_correction_feedback_bridges_to_update_hub():
    """CORRECTION with cited_memory_ids + text_comment triggers memory update."""
    bundle = create_agent_runtime(
        agent_id="test_fb_corr", agent_name="Test", agent_role="tester",
        llm_client=FakeLLM(),
        enable_update_hub=True,
        enable_feedback_collector=True,
    )

    # Inject a memory that will later be corrected
    bundle.agent.ingest.process_message(
        content="I work at Tencent",
        role=MemoryRole.USER,
        agent_id="test_fb_corr",
    )
    _wait_for_queue(bundle.agent.ingest.dispatcher)

    hits = bundle.sqlite.search_with_metadata(
        query="Tencent", agent_id="test_fb_corr", limit=10,
    )
    assert len(hits) >= 1
    memory_id = hits[0].id
    assert hits[0].content == "I work at Tencent"

    # User submits a correction
    event = _make_feedback_event(
        event_id="fbk_correct",
        feedback_type=FeedbackType.CORRECTION,
        cited_memory_ids=[memory_id],
        text_comment="I already left Tencent",
    )
    result = bundle.record_feedback(event)

    assert result["recorded"] is True
    assert memory_id in result["bridged_memory_ids"]
    assert result["failed_memory_ids"] == []

    # Wait for async write from UpdateHub's submit_write_job
    _wait_for_queue(bundle.agent.ingest.dispatcher)

    # Old exact content should no longer appear in active memories
    recent_all = bundle.sqlite.list_recent(limit=50)
    old_contents = {r.content for r in recent_all}
    assert "I work at Tencent" not in old_contents, \
        "Old content should be gone after correction"

    # New exact content should be findable
    assert "I already left Tencent" in old_contents, \
        "New content should appear in recent memories"


# Test 4

def test_non_correction_feedback_does_not_bridge_to_update_hub():
    """THUMBS_UP / THUMBS_DOWN must not trigger update_memory even with text_comment."""
    bundle = create_agent_runtime(
        agent_id="test_fb_noncorr", agent_name="Test", agent_role="tester",
        llm_client=FakeLLM(),
        enable_update_hub=True,
        enable_feedback_collector=True,
    )

    # Inject a memory
    bundle.agent.ingest.process_message(
        content="I work at Tencent",
        role=MemoryRole.USER,
        agent_id="test_fb_noncorr",
    )
    _wait_for_queue(bundle.agent.ingest.dispatcher)

    hits = bundle.sqlite.search_with_metadata(
        query="Tencent", agent_id="test_fb_noncorr", limit=10,
    )
    assert len(hits) >= 1
    memory_id = hits[0].id

    # THUMBS_DOWN with text_comment — should NOT change memory
    event = _make_feedback_event(
        event_id="fbk_down",
        feedback_type=FeedbackType.THUMBS_DOWN,
        cited_memory_ids=[memory_id],
        text_comment="This is wrong but not a correction",
    )
    result = bundle.record_feedback(event)

    assert result["recorded"] is True
    assert result["bridged_memory_ids"] == [], \
        "Non-correction feedback should not bridge to UpdateHub"

    # Memory should be unchanged
    _wait_for_queue(bundle.agent.ingest.dispatcher)
    item = bundle.sqlite.get_by_id(memory_id)
    assert item is not None
    assert item.content == "I work at Tencent", \
        f"Memory should NOT be changed by THUMBS_DOWN, got: {item.content}"


# Test 5

def test_record_feedback_without_enabling_raises():
    """Calling record_feedback() when disabled raises RuntimeError."""
    bundle = create_agent_runtime(
        agent_id="test_fb_err", agent_name="Test", agent_role="tester",
        llm_client=FakeLLM(), enable_feedback_collector=False,
    )

    event = _make_feedback_event()
    try:
        bundle.record_feedback(event)
        assert False, "Should have raised RuntimeError"
    except RuntimeError as e:
        assert "not enabled" in str(e)
