"""
AgentRuntimeBundle: full runtime bundle with optional ConsolidateHub.

Usage::

    # Without consolidation (default, backward-compatible):
    bundle = create_agent_runtime(
        agent_id="zhang_san", agent_name="张三", agent_role="工程师",
        llm_client=llm,
    )

    # With manual consolidation:
    bundle = create_agent_runtime(
        agent_id="zhang_san", agent_name="张三", agent_role="工程师",
        llm_client=llm, enable_consolidation=True,
    )
    bundle.consolidate_once()  # manual trigger, no cron
"""
from dataclasses import dataclass, field
from typing import Optional

from Memory.storage.working_cache import WorkingMemoryCache
from Memory.storage.sqlite_log import SQLiteLogStorage
from Memory.storage.vector_store import VectorStore
from Memory.storage.index_manager import IndexManager
from Memory.hub.consolidate import ConsolidateHub
from Memory.hub.update import UpdateHub
from Memory.policies.retention import RetentionPolicy, RetentionConfig
from Memory.processor.summarizer.long_term import LongTermSummarizer
from Memory.schema.events import ConsolidationTriggerEvent
from Memory.evaluation.online_feedback import FeedbackCollector, FeedbackEvent, FeedbackType
from agents.memory_aware_agent import MemoryAwareAgent
from agents.agent_factory import setup_memory, create_memory_aware_agent


@dataclass
class AgentRuntimeBundle:
    """Holds an agent and its surrounding infrastructure.

    ConsolidateHub and IndexManager are only populated when the runtime is
    created with ``enable_consolidation=True``.
    """

    agent: MemoryAwareAgent
    cache: WorkingMemoryCache
    sqlite: SQLiteLogStorage
    vector_store: VectorStore
    index_manager: Optional[IndexManager] = None
    consolidate_hub: Optional[ConsolidateHub] = None
    update_hub: Optional[UpdateHub] = None
    feedback_collector: Optional[FeedbackCollector] = None

    def consolidate_once(self) -> int:
        """Manually trigger one consolidation cycle.

        Returns the number of recent memory candidates submitted for
        consolidation.  No background cron is ever started.
        """
        if not self.consolidate_hub:
            raise RuntimeError(
                "Consolidation is not enabled for this runtime. "
                "Create with enable_consolidation=True."
            )
        candidates = self.sqlite.list_recent(limit=100)
        if not candidates:
            return 0
        event = ConsolidationTriggerEvent(
            trigger_reason="manual_trigger",
            batch_size=50,
        )
        self.consolidate_hub.handle_event(event)
        return len(candidates)

    def update_memory(self, memory_id: str, new_content: str) -> bool:
        """Correct a memory's content. Wipe-and-replace across all stores."""
        if not self.update_hub:
            raise RuntimeError(
                "UpdateHub is not enabled. Create with enable_update_hub=True."
            )
        return self.update_hub.update_content(memory_id, new_content)

    def update_memory_metadata(self, memory_id: str, **kwargs) -> bool:
        """Update metadata fields on a memory without re-indexing."""
        if not self.update_hub:
            raise RuntimeError(
                "UpdateHub is not enabled. Create with enable_update_hub=True."
            )
        return self.update_hub.update_metadata(memory_id, kwargs)

    def record_feedback(self, feedback_event: FeedbackEvent) -> dict:
        """Record user feedback and bridge corrections to UpdateHub.

        Returns a structured result::

            {
                "recorded": True,
                "bridged_memory_ids": [...],
                "failed_memory_ids": [...],
            }
        """
        if not self.feedback_collector:
            raise RuntimeError(
                "FeedbackCollector is not enabled. "
                "Create with enable_feedback_collector=True."
            )
        self.feedback_collector.record_feedback(feedback_event)

        bridged: list = []
        failed: list = []

        # Bridge: CORRECTION with memory targets + correction text → UpdateHub
        if (
            self.update_hub is not None
            and feedback_event.feedback_type == FeedbackType.CORRECTION
            and feedback_event.text_comment
            and feedback_event.cited_memory_ids
        ):
            for mid in feedback_event.cited_memory_ids:
                try:
                    ok = self.update_memory(mid, feedback_event.text_comment)
                    if ok:
                        bridged.append(mid)
                    else:
                        failed.append(mid)
                except Exception:
                    failed.append(mid)

        return {
            "recorded": True,
            "bridged_memory_ids": bridged,
            "failed_memory_ids": failed,
        }


def create_agent_runtime(
    *,
    agent_id: str,
    agent_name: str,
    agent_role: str,
    llm_client,
    base_system_prompt: str = "",
    time_boost_fn=None,
    enable_consolidation: bool = False,
    enable_update_hub: bool = False,
    enable_feedback_collector: bool = False,
    summarizer=None,
    retention_policy=None,
    feedback_collector=None,
) -> AgentRuntimeBundle:
    """Create a full agent runtime bundle.

    When ``enable_consolidation=True``, an IndexManager and ConsolidateHub
    are created and wired into the memory pipeline.  Cron is **never**
    auto-started — call ``bundle.consolidate_once()`` to trigger a manual
    cycle.

    ``summarizer`` and ``retention_policy`` allow dependency injection for
    testing (defaults to LongTermSummarizer / RetentionPolicy).
    """
    # ── Reuse existing factory for core agent ────────────
    agent, cache, sqlite, vector_store = create_memory_aware_agent(
        agent_id=agent_id,
        agent_name=agent_name,
        agent_role=agent_role,
        llm_client=llm_client,
        base_system_prompt=base_system_prompt,
        time_boost_fn=time_boost_fn,
    )

    index_manager: Optional[IndexManager] = None
    consolidate_hub: Optional[ConsolidateHub] = None
    update_hub: Optional[UpdateHub] = None
    fc: Optional[FeedbackCollector] = None

    needs_index = enable_consolidation or enable_update_hub
    if needs_index:
        # ── Guard: verify dispatch chain ─────────────────
        if not hasattr(agent, "ingest"):
            raise RuntimeError(
                "Agent is missing 'ingest' hub — cannot wire runtime infrastructure."
            )
        if not hasattr(agent.ingest, "dispatcher"):
            raise RuntimeError(
                "Agent.ingest is missing 'dispatcher' — cannot wire runtime infrastructure."
            )
        dispatcher = agent.ingest.dispatcher

        # ── Shared IndexManager ───────────────────────────
        index_manager = IndexManager(
            cache=cache,
            sqlite=sqlite,
            vector_store=vector_store,
            graph_store=None,
        )
        dispatcher.index_manager = index_manager

        # ── Optional: ConsolidateHub ──────────────────────
        if enable_consolidation:
            rp = retention_policy or RetentionPolicy()
            sm = summarizer or LongTermSummarizer()
            consolidate_hub = ConsolidateHub(
                sqlite=sqlite,
                index_manager=index_manager,
                dispatcher=dispatcher,
                summarizer=sm,
                retention_policy=rp,
            )
            dispatcher.register_consolidate_hub(consolidate_hub)
            # NOTE: start_auto_compress_cron() is NOT called.

        # ── Optional: UpdateHub ───────────────────────────
        if enable_update_hub:
            update_hub = UpdateHub(
                sqlite=sqlite,
                index_manager=index_manager,
                dispatcher=dispatcher,
            )

    # ── Optional: FeedbackCollector (standalone) ─────────
    if enable_feedback_collector:
        fc = feedback_collector or FeedbackCollector()

    return AgentRuntimeBundle(
        agent=agent,
        cache=cache,
        sqlite=sqlite,
        vector_store=vector_store,
        index_manager=index_manager,
        consolidate_hub=consolidate_hub,
        update_hub=update_hub,
        feedback_collector=fc,
    )
