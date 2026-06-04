# Cyber Town v1 Benchmark Results

> 2026-06-03 | Python 3.9.12 | Windows 11 | LLM mocked (50ms delay)

## Retrieval Quality

| Metric | Mock (keyword) | Real (ChromaDB) |
|--------|----------------|-----------------|
| Precision@3 | 0.07 | 0.12 |
| Recall@3 | 0.30 | 0.61 |
| MRR@3 | 0.60 | 0.87 |
| Temporal Precision@1 | 0.60 | 0.60 |
| avg latency | 0.6 ms | 15.0 ms |
| p50 latency | 0.6 ms | 12.8 ms |
| p95 latency | 0.8 ms | 38.0 ms |

**Mock** (default): keyword-only SQLite `LIKE` matching, `MockVectorStore` (no
embedding). Latency excludes embedding. CI-compatible.

**Real**: ChromaDB + BGE-small-zh (384d). Latency includes on-the-fly embedding
computation per query + ChromaDB ANN search. Recall@3 improves ~2x, MRR@3
improves ~1.5x over keyword-only. Precision@3 is limited by benchmark design
(expected set vs semantically-relevant-but-not-expected items). Run via
`python benchmarks/retrieval_bench.py --real`.

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
