# Multi-Agent Collaboration Flow

## Why Coordinator-Driven Collaboration

We chose a **centralized TaskCoordinator** rather than direct agent-to-agent
negotiation for three reasons:

1. **No infinite chat loops.** When agents talk directly to each other, every
   reply becomes a new event, which triggers another reply — requiring complex
   termination detection. A Coordinator publishes `task_assigned`, agents
   respond with exactly one `task_done`, and that's the end of the loop.

2. **Deterministic task completion.** The Coordinator knows how many subtasks
   were created and can check `collect_results()` to confirm all are done
   before calling `synthesize_results()`. Direct negotiation has no such
   guarantee.

3. **Coordinator is not an agent.** It has no memory, no personality, no state
   transitions. It's a thin runtime component that orchestrates through the
   EventBus — zero overhead, maximum clarity.

## Event Flow

```
                  User Request
                       │
                       ▼
┌──────────────────────────────────────────┐
│            TaskCoordinator                │
│                                            │
│  decompose_with_llm(request, roles)       │
│    → [subtask_PM, subtask_Eng, subtask_UI]│
│                                            │
│  for each subtask:                        │
│    assign_by_role(subtask, agents)        │
│    → EventBus.publish(task_assigned)      │
└──────────┬──────────┬──────────┬──────────┘
           │          │          │
    ┌──────▼──┐ ┌─────▼───┐ ┌───▼──────┐
    │ PM Agent│ │Eng Agent│ │UI Agent  │
    │ Li Si   │ │Zhang San│ │Wang Wu   │
    │ respond │ │ respond │ │ respond  │
    └────┬────┘ └────┬────┘ └────┬─────┘
         │           │           │
         ▼           ▼           ▼
    EventBus.publish(task_done) × 3
         │           │           │
         └───────────┼───────────┘
                     ▼
┌──────────────────────────────────────────┐
│            TaskCoordinator                │
│                                            │
│  _on_collab_event(task_done) × 3         │
│    → updates task status to DONE          │
│    → collects results                     │
│                                            │
│  synthesize_results(parent_id, request)   │
│    → LLM merges 3 results into final plan │
└──────────────────────────────────────────┘
                     │
                     ▼
               Final Plan
```

## Task Lifecycle

```
CREATED ──assign_task()──▶ ASSIGNED ──agent.respond()──▶ DONE
   │                            │                         │
   │                            │                         │
   └──assign_by_role()──────────┘                         │
   (no matching role)                                     │
        │                                                 │
        ▼                                                 ▼
     FAILED                                     result stored in
                                              coordinator._results
```

Each state transition is explicit and traceable through `coordinator._tasks`.

## Memory Isolation During Collaboration

Each agent executes its subtask using its **own memory**. When Li Si (PM)
responds to "Write a requirements document", she retrieves from *her* SQLite
and *her* ChromaDB — she cannot access Zhang San's (engineer) memories.

This is enforced by `agent_id` at every layer:
- `IngestHub.process_message(agent_id="li_si")` → only Li Si's stores
- `RetrieveHub.retrieve(agent_id="li_si")` → only Li Si's vectors
- `SQLiteLogStorage.search_with_metadata(agent_id="li_si")` → only Li Si's rows

The Coordinator never writes or reads agent memories — it only routes tasks and
collects text results.

## LLM Planning Layered on Deterministic Protocol

The LLM only handles **two things**:

1. **Decomposition** (`decompose_with_llm`): LLM takes a natural-language
   request and outputs structured JSON. The JSON is validated — unknown roles
   are discarded, malformed subtasks are skipped. If the LLM fails entirely,
   the function returns `[]` without crashing.

2. **Synthesis** (`synthesize_results`): LLM takes collected agent outputs
   and merges them into a final plan. If the LLM fails, results are concatenated
   as a fallback.

Everything between decomposition and synthesis — task assignment, event routing,
execution, result collection — is **deterministic code** that never calls the
LLM.

```
LLM layer:        decompose_with_llm()          synthesize_results()
                      │                               ▲
Deterministic         │                               │
layer:          assign → EventBus → execute → collect → done
```

This means the collaboration protocol itself is testable end-to-end with
`FakeLLM` — no real Ollama needed in CI.
