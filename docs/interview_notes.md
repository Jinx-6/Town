# Interview Notes — Cyber Town

## 30-Second Project Pitch

"Cyber Town is a multi-agent simulation runtime I built from scratch in Python.
Each agent has a 4-layer cognitive memory system — working memory, episodic log,
semantic vector store, and an optional knowledge graph. Agents communicate
through a publish-subscribe EventBus and can collaborate on complex tasks
through an LLM-driven TaskCoordinator. 165 tests, CI, benchmarks, all green.
The core insight is that **memory is not just RAG** — it's a lifecycle of
writing, retrieving, compressing, correcting, and forgetting."

## Why This Is Not Ordinary RAG

Most "AI memory" projects do this:

```
user query → vector search → stuff into prompt → LLM answers
```

Cyber Town does this:

```
user input → intent classifier (4 classes)
  → intent-driven routing (different storage targets per intent)
  → multi-source retrieval (SQLite keyword + ChromaDB semantic + Neo4j graph)
  → 3D scoring (semantic 60% + recency 30% + importance 10%)
  → XML-partitioned prompt assembly
  → LLM generation
  → write-back with conflict detection
  ── hours later ──
  → ConsolidateHub: compress old episodic into semantic summaries
  → RetentionPolicy: delete expired memories
```

It's a **simulation runtime**, not a chatbot with a vector database.

## Memory Lifecycle (How to Explain in 2 Minutes)

1. **When the user says something**, IntentClassifier decides what kind of
   statement it is. A fact ("I fixed the server yesterday") goes to all 4
   storage layers. Chitchat ("nice weather") only goes to short-term cache.

2. **When the user asks a question**, we don't just do one vector search. A
   QueryPlanner decomposes it, runs SQLite keyword + ChromaDB semantic search
   in parallel, scores results on 3 dimensions, deduplicates, and assembles
   them into XML-tagged sections in the prompt.

3. **Once per day** (triggered manually, not by cron), ConsolidateHub walks
   through old episodic memories. RetentionPolicy decides: compress into a
   semantic summary (if >1 day old), or physically delete (if >30 days old).

4. **When a memory is wrong**, FeedbackCollector accepts a CORRECTION event
   and bridges it to UpdateHub, which does a wipe-and-replace across all
   storage layers.

## Agent Runtime (How to Explain in 1 Minute)

"Agents don't talk to each other directly — they subscribe to topics on an
EventBus. When the Simulator injects a seed message, each AgentWorker receives
it, runs the full memory pipeline, and publishes a reply event. Those replies
become new events, which trigger more replies. A SchedulerPolicy caps total
events and per-agent output to prevent infinite loops.

Crucially, every agent's memory is isolated by `agent_id`. If Zhang San learns
a password, Li Si cannot retrieve it — the ChromaDB query, the SQLite WHERE
clause, and the Neo4j Cypher all filter by `agent_id`."

## Multi-Agent Collaboration (How to Explain in 1 Minute)

"For complex tasks, a TaskCoordinator uses LLM to decompose a request into
role-specific subtasks. 'Plan a login page feature' becomes three subtasks:
PM writes requirements, engineer estimates effort, designer sketches UI.

The Coordinator publishes `task_assigned` events to the EventBus. Each
CollabAgentWorker picks up its task (filtered by `target_agent_id`), executes
it using its own memory and LLM, and returns a `task_done` event. The
Coordinator collects all results and calls the LLM one more time to synthesize
a final plan.

The key decisions: Coordinator is a runtime component, not an agent — no memory
overhead. Agents communicate through the Coordinator, not directly — no infinite
chat loops. LLM only touches decomposition and synthesis — everything in between
is deterministic code, testable with FakeLLM."

## Hard Engineering Decisions

| Decision | Why |
|----------|-----|
| Coordinator as runtime component, not agent | Agents have memory overhead + state machines. Coordinator just routes events. |
| Coordinator-driven, not agent-to-agent | Direct agent chat creates infinite loops. Coordinator guarantees 1-request-1-response per subtask. |
| Manual consolidation, not auto-cron | Cron in tests = flaky. Manual trigger is deterministic and debuggable. |
| `_should_update_relationship` with 5 conditions | Only direct agent-to-agent messages update affinity. System/user/broadcast events must not. |
| `task_done` filtered at `CollabAgentWorker` entry | Without this, task_done events would be treated as conversation and generate replies → infinite loop. |
| Wipe-and-replace for memory update | Safer than in-place editing: cascade-delete old → write new ensures index consistency across SQLite + ChromaDB + Neo4j. |
| MockVectorStore + FakeLLM for all tests | 165 tests, no Ollama, no ChromaDB in CI. Tests are fast and repeatable. |

## What I Would Improve Next

1. **Persistent EventStore.** EventBus events are in-memory — restart loses
   history. A SQLite-backed EventStore would give the runtime real persistence.
2. **Web frontend.** Godot or a simple React UI would make this demo-able to
   non-engineers.
3. **Shared MockLLMClient.** Each test file defines its own FakeLLM. Extracting
   a shared test utility module would reduce duplication.
4. **`pyproject.toml`.** Proper packaging instead of `requirements.txt` +
   `sys.path.insert`.

## Resume Bullet Suggestions

Pick 3-4 based on the role:

> - Designed and implemented a 4-layer cognitive memory architecture (working /
>   episodic / semantic / graph) for multi-agent AI agents — not just RAG
> - Built an event-driven multi-agent runtime with publish-subscribe EventBus,
>   agent-level memory isolation, and programmable scheduling policies
> - Implemented a complete memory lifecycle: intent-driven write routing, 3D
>   retrieval scoring, policy-based retention/forgetting, and wipe-and-replace
>   memory correction
> - Designed an LLM-driven task coordinator for multi-agent collaboration:
>   decompose → role-based assignment → independent execution → synthesis
> - 165 tests, CI/CD pipeline, benchmarks for retrieval quality and throughput
> - Python / ChromaDB + BGE-small-zh / SQLite / Ollama / pytest

## Common Interview Questions (and Answers)

**Q: Why not LangChain / CrewAI?**
A: I wanted to understand and control every layer. LangChain abstracts away the
memory pipeline and agent communication — I wanted to build the EventBus,
scoring policy, and retention logic myself to truly understand the problem.

**Q: What happens if the LLM decomposition returns nonsense?**
A: The JSON is validated against a strict schema. Unknown roles are skipped.
Malformed subtasks are discarded. If parsing fails entirely, `decompose_with_llm`
returns `[]` — the system degrades gracefully rather than crashing.

**Q: How do you prevent memory leaks between agents?**
A: `agent_id` is threaded through every storage call. ChromaDB queries use
`where={"agent_id": agent_id}`. SQLite uses parameterized WHERE clauses. Neo4j
Cypher uses `WHERE ALL(rel IN r WHERE rel.agent_id = $agent_id)`. 8 dedicated
isolation tests verify cross-agent contamination is impossible.

**Q: Why not use a real message queue (Kafka/RabbitMQ)?**
A: Scope. This is a single-process simulation runtime. The EventBus protocol
(subscribe/publish/dispatch) is the same pattern — migrating to Kafka would be
an infrastructure change, not an architecture change.
