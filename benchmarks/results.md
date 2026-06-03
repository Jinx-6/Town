# Cyber Town v1 Benchmark Results

> 2026-06-03 | Python 3.9.12 | Windows 11 | LLM mocked (50ms delay)

## Retrieval Quality

| Metric | Value |
|--------|-------|
| Precision@3 | 0.07 |
| Recall@3 | 0.22 |
| MRR@3 | 0.40 |
| Temporal Precision@1 | 0.20 |
| avg latency | 0.7 ms |
| p50 latency | 0.7 ms |
| p95 latency | 0.8 ms |

**Note**: These numbers reflect keyword-only retrieval via SQLite `LIKE` matching
with `MockVectorStore` (no embedding). The current `search_with_metadata`
implementation uses space-delimited keyword splitting, which has poor recall on
Chinese text without explicit whitespace. In production with ChromaDB +
BGE-small-zh embeddings, semantic recall is expected to be significantly higher.
Latency includes SQLite + in-memory vector lookup only (no embedding computation).

## Agent Respond Latency

| Scenario | p50 | p95 | avg |
|----------|-----|-----|-----|
| chitchat | 279.8 ms | 314.3 ms | 280.3 ms |
| fact_recall | 281.9 ms | 295.5 ms | 278.9 ms |
| qa_with_tools | 334.8 ms | 355.6 ms | 335.8 ms |

**Note**: Measurements include a fixed 50ms mock LLM delay. The remaining
~230-280ms is overhead from classify → retrieve → assemble → ingest pipeline,
with the majority spent in `AsyncDispatcher` background thread flushing.
Tool calling adds ~55ms (tool registry dispatch + second LLM call).

## Pub/Sub Throughput

| Agents | Bus events/sec | Worker events/sec | Elapsed (200 evt) |
|--------|---------------|-------------------|--------------------|
| 2 | 1,407 | 1,414 | 142.2 ms |
| 5 | 36,499 | 146,179 | 5.5 ms |
| 10 | 20,535 | 184,917 | 9.7 ms |

**Note**: Throughput measured with `FakeAgent` (no LLM, no memory stores).
EventBus routing is the bottleneck at low agent counts; worker fan-out
dominates at higher counts. Linear-ish scaling from 2→10 agents.
