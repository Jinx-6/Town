# Cyber Town — Multi-Agent Simulation Runtime with Long-Term Memory

Cyber Town is a multi-agent simulation runtime where each agent has an independent
4-layer cognitive memory system. Agents communicate autonomously via a
publish-subscribe EventBus — no scripted orchestration required — and can
collaborate on complex tasks through an LLM-driven TaskCoordinator.

Unlike ordinary RAG, Cyber Town implements a **complete memory lifecycle**:
write → retrieve → compress → correct → forget. Every component is isolated
by `agent_id`, ensuring that agent memories never leak across the multi-agent
runtime.

**Tech stack**: Python / Ollama / ChromaDB + BGE-small-zh / SQLite / pytest
(Neo4j optional).

**165 tests, all passing.**

## Key Features

- **4-layer cognitive memory**: Working (L0) → Episodic (L1) → Semantic (L2) → Graph (L3)
- **Memory lifecycle**: Ingest → Retrieve → Consolidate → Update → Feedback correction
- **Agent-level memory isolation**: `agent_id` scoping across all storage layers
- **Event-driven multi-agent runtime**: Pub/sub EventBus with SchedulerPolicy
- **RelationshipManager**: 5-tier affinity system with LLM sentiment analysis
- **Tool Calling + Skill Layer**: 3 built-in tools, extensible skill registry
- **LLM-orchestrated collaboration**: Task decomposition → role-based assignment → synthesis
- **Manual memory consolidation**: ConsolidateHub with RetentionPolicy (no auto-cron)
- **Feedback-driven correction**: FeedbackCollector bridges CORRECTION → UpdateHub
- **Benchmarks**: Retrieval quality, agent latency, pub/sub throughput
- **CI**: GitHub Actions on push/PR

## Architecture Overview

```
┌──────────────────────────────────────────────────────┐
│                  User / Demo Script                   │
├──────────────────────┬───────────────────────────────┤
│   TaskCoordinator    │         Simulator              │
│   (LLM orchestration)│    (Pub/Sub orchestration)     │
└──────────┬───────────┴───────────────┬───────────────┘
           │                           │
           ▼                           ▼
┌──────────────────────────────────────────────────────┐
│                     EventBus                          │
│   subscribe / publish / dispatch / dedup / quota      │
└──────┬───────────────────────────────────┬───────────┘
       │                                   │
       ▼                                   ▼
┌──────────────┐                  ┌───────────────────┐
│AgentWorker   │                  │CollabAgentWorker  │
│(conversation)│                  │(task execution)   │
└──────┬───────┘                  └────────┬──────────┘
       │                                   │
       └───────────────┬───────────────────┘
                       │ respond()
                       ▼
┌──────────────────────────────────────────────────────┐
│                MemoryAwareAgent                       │
│  classify → retrieve → assemble → [tool] → generate  │
│  → ingest                                            │
└──────────────────────┬───────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────┐
│                 Memory Runtime                        │
│  L0 WorkingCache  │  L1 SQLite  │  L2 ChromaDB       │
│  Policies: Scoring / Retention / Retrieval / Update  │
│  Hubs: Ingest / Retrieve / Consolidate / Update       │
└──────────────────────────────────────────────────────┘
```

## Memory Lifecycle

### Ingest — Intent-driven write routing

```
User input → IntentClassifier (4 classes)
  ├─ fact_statement  → [CACHE, SQLITE, VECTOR, GRAPH]
  ├─ task_instruction → [CACHE, SQLITE]
  ├─ qa_query        → [CACHE, SQLITE]
  └─ chitchat        → [CACHE]
  → IngestHub.process_message()
    → L0 Cache (sync)
    → AsyncDispatcher → L1 SQLite + L2 ChromaDB embedding
```

### Retrieve — Hybrid search with 3D scoring

```
Query → QueryPlanner → multi-query rewrite
  → SQLite keyword + ChromaDB semantic search
  → MemoryScorer: semantic (60%) + recency (30%) + importance (10%)
  → dedup + sort → PromptAssembler XML assembly
```

### Consolidate — Manual memory compression and GC

```python
bundle = create_agent_runtime(..., enable_consolidation=True)
bundle.consolidate_once()  # one manual cycle, no cron
# RetentionPolicy: compress episodic → semantic, delete expired
```

### Update — Wipe-and-replace memory correction

```python
bundle.update_memory(memory_id, "corrected content")
# IndexManager cascade-deletes old → AsyncDispatcher writes new across all stores
```

### Feedback — Human correction bridges to UpdateHub

```python
event = FeedbackEvent(type=CORRECTION, cited_memory_ids=[mid],
                      text_comment="corrected info")
bundle.record_feedback(event)
# → JSONL logged → UpdateHub.update_content() called automatically
```

## Multi-Agent Collaboration

LLM-driven task orchestration via `TaskCoordinator` + `CollabAgentWorker`:

```
User Request: "Plan a feature release for the login page"
  │
  ▼
TaskCoordinator.decompose_with_llm()     ← LLM splits into role-specific subtasks
  ├─ [PM] Write requirements document
  ├─ [Engineer] Estimate technical effort
  └─ [Designer] Design UI layout
  │
  ▼
assign_by_role() → EventBus.publish(task_assigned)
  │
  ▼
CollabAgentWorker._handle_task()         ← each agent executes independently
  → agent.respond(task.description) → return task_done event
  │
  ▼
TaskCoordinator.synthesize_results()     ← LLM merges into final plan
```

Key design decisions:
- Coordinator is a runtime component, **not an agent** — no memory overhead
- Agents communicate **through** the Coordinator, not directly — avoids infinite chat loops
- `task_assigned` / `task_done` use existing `Event.type` field — no protocol changes
- `CollabAgentWorker` extends `AgentWorker` — inherits all filtering and relationship logic

## Demo Commands

```bash
# Multi-agent conversation
python demo_multi_agent.py --mock --turns 5

# Multi-agent LLM collaboration (3 agents)
python demo_collab.py --mock

# Single-agent interactive CLI
python demo_cli.py

# Full test suite
python -m pytest Test/ -q
```

## Testing

**165 tests, 14 test files, all passing.** CI via GitHub Actions on push/PR.

| Area | Files | Tests |
|------|-------|-------|
| Memory core | test_schema, test_storage_l1, test_memory_pipeline, test_memory_isolation, test_memory_retrieval_quality | ~63 |
| Agent | test_memory_aware_agent, test_agent_tools | ~40 |
| Pub/Sub | test_multi_agent_pubsub | 21 |
| Skills | test_skills | 12 |
| Hubs & Runtime | test_relationship_integration, test_consolidate_integration, test_update_integration, test_feedback_integration | 18 |
| Collaboration | test_collab | 14 |
| **Total** | **14 files** | **165** |

Tests use `FakeLLM` and `MockVectorStore` — no Ollama or ChromaDB required in CI.

## Roadmap

### Done
- [x] 4-layer Memory (L0-L3) with full lifecycle
- [x] EventBus + AgentWorker + Simulator pub/sub runtime
- [x] Tool Calling + Skill Layer
- [x] RelationshipManager (5-tier affinity)
- [x] ConsolidateHub / UpdateHub / FeedbackCollector
- [x] TaskCoordinator + CollabAgentWorker (LLM collaboration)
- [x] Benchmarks + CI + 165 tests

### Future
- [ ] Persistent EventStore (event log survives restart)
- [ ] Web UI or Godot frontend
- [ ] `pyproject.toml` packaging
- [ ] Shared `MockLLMClient` test utility

## Limitations

- **LLM dependency**: real demos need Ollama locally; mock mode for CI/tests
- **L3 Neo4j**: code complete but disabled by default (`graph_store=None`)
- **Single process**: EventBus is in-memory, no cross-machine communication
- **No frontend**: CLI only, no Web UI / Godot client
- **No persistent event log**: EventBus events lost on restart
