# Resume Versions — Cyber Town

> Use this page to pick the right framing for the audience.

---

## Version A: Conservative (Honest + Verifiable)

*Choose this for: senior engineers, FAANG-style interviews, situations where
overstatement will be caught and punished.*

**Cyber Town — Multi-Agent Simulation Runtime**

Built a multi-agent simulation runtime in Python where each agent has independent
4-layer cognitive memory (working / episodic / semantic / graph). Agents
communicate through an in-process publish-subscribe EventBus and can collaborate
on complex tasks via an LLM-driven TaskCoordinator that decomposes high-level
requests into role-assigned subtasks and synthesizes results.

- Designed intent-driven memory routing: facts go to all 4 storage layers,
  chitchat stays in short-term cache — preventing memory pollution
- Implemented hybrid retrieval (SQLite keyword + ChromaDB semantic search) with
  3D scoring (semantic, recency, importance) and temporal re-ranking
- Built wipe-and-replace memory correction pipeline: user CORRECTION →
  FeedbackCollector → UpdateHub → cascade-delete across SQLite + ChromaDB
- Wrote 165 tests (pytest, SQLite, mock infra), CI on GitHub Actions, benchmarks
  for retrieval quality / latency / throughput
- Stack: Python, SQLite, ChromaDB (BGE-small-zh), Ollama (qwen3:8b), pytest

---

## Version B: Stronger (Confident + Specific)

*Choose this for: startups, AI-engineering roles, roles where "built from scratch"
is a differentiator.*

**Cyber Town — Multi-Agent Memory Simulation System**

Designed and implemented a complete multi-agent simulation platform from scratch
in Python. Each agent runs an independent 4-tier cognitive memory architecture
(working memory → episodic log → semantic vector store → knowledge graph), with a
full lifecycle: intent-driven write routing, hybrid retrieval (keyword + semantic
+ graph), policy-based compression/retention, and feedback-driven correction.

The multi-agent layer uses a custom publish-subscribe EventBus — agents
communicate autonomously without scripted dialog trees. A LLM-driven
TaskCoordinator decomposes complex requests, assigns role-specific subtasks,
collects results, and synthesizes a unified plan. Agent memory is fully isolated
at the storage level (agent_id enforcement in all SQL/vector/graph queries).

- 4-layer cognitive memory: L0 deque cache → L1 SQLite episodic → L2 ChromaDB +
  BGE-small-zh semantic → L3 Neo4j knowledge graph (code-complete, disabled)
- Intent classifier with 4-way routing, LLM query planner, XML-partitioned prompt
  assembler, 3D retrieval scoring, tiered TTL retention (30d/365d)
- Event-driven multi-agent runtime: EventBus with scheduling policies,
  AgentWorker protocol adapter, 5-tier relationship affinity system
- LLM collaboration: coordinator decomposition → role-based assignment →
  independent agent execution → synthesis — all deterministic between LLM calls
- 165 tests in 14 files, CI pipeline, 3 benchmarks (retrieval quality, agent
  latency, pub/sub throughput), comprehensive docs
- Python / SQLite / ChromaDB / Neo4j / Ollama / pytest / GitHub Actions

---

## Interview Talking Points (6-8 bullets to deploy in conversation)

1. **"Memory is a lifecycle, not a database lookup."**
   Most AI memory demos do `search → stuff into prompt → generate`. Cyber Town
   does classify → route → hybrid retrieve → 3D score → assemble → generate →
   write-back with conflict detection → periodic consolidat​e → forget. Each
   stage has a policy engine that can be tuned independently.

2. **"agent_id isolation at the storage layer."**
   Every read/write to SQLite, ChromaDB, and Neo4j includes an `agent_id`
   filter. This is enforced at the driver level — it's structurally impossible
   (not just "best practice") for an agent to read another agent's memories.
   The `test_memory_isolation.py` suite (8 tests) proves this.

3. **"LLM only touches the fuzzy parts."**
   Task decomposition and final synthesis use the LLM. Everything in between —
   assignment, routing, execution, result collection — is deterministic Python
   code. This means the collaboration system is testable with FakeLLM, and the
   14 collaboration tests run in CI without Ollama.

4. **"EventBus, not hardcoded dialog."**
   Agents don't have scripted conversations. They subscribe to topics on an
   EventBus. A Simulator injects seed events; handlers produce reply events
   which become new stimuli. The system self-generates conversation without any
   conversation tree. SchedulerPolicy caps total events and per-agent output to
   prevent infinite loops.

5. **"Mock infrastructure as a first-class design choice."**
   164 of 165 tests run without Ollama, without ChromaDB, without
   sentence-transformers — just Python + SQLite. MockVectorStore (in-memory
   dict) and FakeLLM (canned strings) are how we test the full memory pipeline
   deterministically. The 1 test file that needs Ollama auto-skips. This is not
   a shortcut — it's an engineering decision that makes CI fast and repeatable.

6. **"Coordinator as a runtime component, not an agent."**
   The TaskCoordinator subscribes to the EventBus like any other component,
   but it has no memory, no personality, no state machine. It's a thin
   orchestrator — decompose, assign, collect, synthesize. This avoids giving
   the coordinator its own memory (overhead) and avoids the "coordinator learns
   wrong lessons from past collaborations" problem.

7. **"Every design decision has a documented reason."**
   Why SQLite for L1 (not a vector DB)? Chinese keyword matching without
   whitespace tokenization is unreliable, so L2 ChromaDB compensates. Why
   manual consolidation (not auto-cron)? Cron in tests is flaky. Why
   wipe-and-replace (not in-place edit)? Cascade delete + rewrite keeps
   SQLite/ChromaDB/Neo4j indexes consistent. These are in
   `docs/project_audit_packet.md`.

8. **"Chinese comments are a feature, not a bug."**
   The source code contains Chinese comments for domain-specific concepts
   (e.g., 时间记忆混淆 = "temporal memory confusion"). All documentation and
   public-facing README material is English. This demonstrates ability to work
   across language barriers.

---

## 5 Things NOT to Exaggerate

1. **Don't claim this is "production-ready."**
   It's a single-process simulation runtime. The EventBus is in-memory. Events
   are lost on restart. Scaling to multiple machines requires replacing the
   EventBus with a network message queue (explicitly noted as a limitation).

2. **Don't claim "Neo4j knowledge graph" as a working feature.**
   The `GraphStore` is code-complete (CRUD, Cypher queries, cascade delete) but
   disabled by default because Neo4j requires a separate Java server. The demo
   runs on SQLite + ChromaDB only. Mention Neo4j as "code-complete, disabled by
   default for setup simplicity" — not as a running feature.

3. **Don't claim the retrieval benchmark numbers are impressive.**
   Precision@3 = 0.12 (ChromaDB) is low. This is a benchmark design limitation
   — precision penalizes semantically-relevant-but-not-in-expected-set results.
   Recall@3 = 0.61 and MRR@3 = 0.87 are the better metrics. Be ready to explain
   this if asked.

4. **Don't claim "Chinese NLP expertise" from using BGE-small-zh.**
   It's an off-the-shelf embedding model loaded via `sentence-transformers`.
   The Chinese-specific work is the temporal keyword extraction (`昨天` / `前天`
   / `最近`) and conflict detection — reasonable regex-level work, not NLP
   research.

5. **Don't claim "165 tests with 100% coverage."**
   The test suite covers the memory pipeline, agent system, and collaboration
   — but `state_manager.py`, `config.py`, `logger.py`, and `LLMClient.py` have
   no dedicated tests. Coverage is strong on the core path, not 100%.
   `docs/project_audit_packet.md` Section 5.4 lists exact coverage gaps.
