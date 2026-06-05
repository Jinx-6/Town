# Cyber Town — Demo Runbook

> Last verified: 2026-06-05 | Branch: `mvp-agent-demo` | Python 3.9+

## 1. Environment Requirements

| Requirement | Version | Required For | Notes |
|-------------|---------|-------------|-------|
| Python | 3.9+ | Everything | Tested on 3.9.12 / Windows 11 |
| pip | 21+ | Install dependencies | |
| Ollama | latest | Real LLM demos only | Optional — `--mock` modes work without it |
| Ollama model | qwen3:8b | Real LLM demos only | ~5 GB download, any OpenAI-compatible model works |
| Disk | ~1 GB | ChromaDB + model | Mock mode needs <100 MB |

## 2. Install Dependencies

```bash
# Clone and enter project
git checkout mvp-agent-demo
cd Town

# Create virtualenv (recommended)
python -m venv venv
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# Install all dependencies
pip install -r requirements.txt
```

**What this installs**: `openai`, `pydantic`, `pydantic-settings`, `chromadb`, `sentence-transformers`, `pytest`.

The first `chromadb` import will download `BGE-small-zh` (~130 MB) automatically if not cached.

## 3. Environment Configuration

```bash
# Copy the example env file
cp .env.example .env
```

Default `.env` content:
```
OLLAMA_API_KEY=ollama
OLLAMA_BASE_URL=http://localhost:11434/v1
OLLAMA_MODEL_ID=qwen3:8b
MEMORY_CAPACITY=10
TOWN_TICK_SECONDS=300
```

The `.env` file is only needed for real LLM demos. Mock demos ignore it.

## 4. Run Mock Demos (No Ollama Required)

All mock demos use `FakeLLM` (canned responses) and `MockVectorStore` (in-memory dict). They run in seconds and validate the full pipeline except real LLM calls.

### 4.1 Multi-Agent Pub/Sub (Default Scenario)

```bash
python demo_multi_agent.py --mock
```

Expected output (summary):
```
============================================================
  赛博小镇 — 发布订阅多 Agent 对话
============================================================
  频道: town.public
  参与者: 张三, 李四
  最大消息数: 10
  模式: mock (dry-run)
============================================================

[seed] 「大家好，今天天气真不错！」

张三 > [张三 模拟回复 #1] 收到了「大家好，今天天气真不错！」

李四 > [李四 模拟回复 #1] 收到了「大家好，今天天气真不错！」

...

============================================================
  运行结束
============================================================
  张三 (zhang_san): 处理了 5 条消息
  李四 (li_si): 处理了 4 条消息
  总计 dispatch: 9 个事件
```

### 4.2 Memory Isolation Demo

```bash
python demo_multi_agent.py --scenario memory_isolation --mock
```

Validates that `agent_id` isolation works — Zhang San's password stays in Zhang San's memory, Li Si's in Li Si's. The isolation check section at the end prints each agent's password-related memories.

### 4.3 Temporal Memory Recall

```bash
python demo_multi_agent.py --scenario temporal_memory --mock
```

Injects "yesterday: fixed generator" and "day-before-yesterday: fixed water dispenser" seeds, then queries "what did I help you with yesterday?".

### 4.4 Skill Observation

```bash
python demo_multi_agent.py --scenario skill_observation --mock
```

Agents use `ObservePublicEventSkill` to monitor public events.

### 4.5 Tool Calling

```bash
python demo_multi_agent.py --scenario tool_calling --mock
```

Agent uses `search_own_memory`, `get_current_time`, and `get_agent_state` tools.

### 4.6 Multi-Agent LLM Collaboration

```bash
python demo_collab.py --mock
```

Expected output:
```
============================================================
  赛博小镇 — 多 Agent LLM 协作演示
  模式: mock (dry-run)
============================================================

[请求] 帮我做新功能「用户登录页面」的完整方案

[Coordinator] 正在分解任务...
  → 分解为 3 个子任务:
    [产品经理] 撰写需求文档
    [Python工程师] 评估技术方案
    [UI设计师] 设计UI布局

[Coordinator] 正在分配任务...
  OK 撰写需求文档 → li_si
  OK 评估技术方案 → zhang_san
  OK 设计UI布局 → wang_wu

[EventBus] 运行中...
  共分发 6 个事件

[Coordinator] 正在汇总结果...
  收集到 3 个结果

============================================================
  最终方案
============================================================
【协作方案汇总】
...（综合方案文本）
============================================================
```

Also works with `--verbose` to print the full event trace.

### 4.7 Temporal Memory Reproducibility

```bash
python demo_reproduce.py
```

This is a pure-deterministic script — no LLM dependency. Injects two seed memories with specific timestamps and runs two queries ("yesterday" / "day before yesterday") with full debug retrieval tracing. Pass/Fail verdict is printed.

### 4.8 Single-Agent Autonomous Mode (Needs Ollama)

```bash
# Requires Ollama running + model downloaded
python demo_cli.py --auto 3
```

Runs 3 autonomous turns — the agent talks to itself via internal stimuli.

## 5. Run Real Ollama Demos

### 5.1 Start Ollama

```bash
# Terminal 1: start Ollama server
ollama serve

# Terminal 2: pull the model (first time only, ~5 GB)
ollama pull qwen3:8b
```

Verify Ollama is running:
```bash
curl http://localhost:11434/api/tags
# Should return JSON with model list including "qwen3:8b"
```

### 5.2 Interactive Single-Agent CLI

```bash
python demo_cli.py
```

Interactive prompt:
```
=======================================================
  赛博小镇 — 单智能体记忆驱动对话演示
=======================================================

[System] 正在初始化记忆引擎 (SQLite + ChromaDB)...
[System] 记忆引擎就绪

=======================================================
  张三（Python工程师）已上线
  输入消息开始对话，/quit 退出，/reset 重置记忆，/auto N 自主运行
=======================================================

You > 你好，我叫进喜
张三 > 你好进喜！欢迎来到赛博小镇，我是张三，有什么需要帮忙的吗？

You > 我昨天帮你修了发电机
张三 > 太感谢了！我会记住你昨天帮我修了发电机这件事。发电机最近确实有点老化。

You > 你还记得我昨天帮我做了什么吗？
张三 > 当然记得！你昨天帮我修了发电机，真是帮了大忙了。
```

Flags:
- `--reset` — wipe memory before starting
- `--debug-retrieval` — print full retrieval trace per turn
- `--auto N` — run N autonomous turns and exit

### 5.3 Real Multi-Agent Pub/Sub

```bash
python demo_multi_agent.py
# or specific scenario:
python demo_multi_agent.py --scenario temporal_memory --debug
```

### 5.4 Real Multi-Agent Collaboration

```bash
python demo_collab.py
```

Uses `LLMClient` (Ollama) for both task decomposition/synthesis and per-agent responses.

### 5.5 Full Audit Packet

```bash
# This command runs: pytest + 3 benchmarks + all mock demos
# (no single script — run each step below)
```

## 6. Run Tests

### 6.1 Full Test Suite

```bash
python -m pytest Test/ -q
```

Expected output:
```
........................................................................ [ 43%]
........................................................................ [ 87%]
.....................                                                    [100%]
165 passed in 517.01s (0:08:37)
```

**Note**: The full suite takes ~8.5 minutes. 164/165 tests run without Ollama — only `test_memory_aware_agent.py` requires Ollama and auto-skips if unavailable.

### 6.2 Quick Smoke Test (Fail-Fast)

```bash
python -m pytest Test/ --maxfail=1 -q
```

### 6.3 Subset by Keyword

```bash
python -m pytest Test/ -k "memory" -q       # Memory pipeline tests
python -m pytest Test/ -k "isolation" -q    # Memory isolation tests
python -m pytest Test/ -k "collab" -q       # Collaboration tests
python -m pytest Test/ -k "pubsub" -q       # Pub/sub tests
```

### 6.4 Verbose Mode

```bash
python -m pytest Test/ -v --tb=short
```

## 7. Run Benchmarks

### 7.1 Retrieval Quality

```bash
# Mock mode (keyword-only, no embedding, fast)
python benchmarks/retrieval_bench.py

# Real mode (ChromaDB + BGE-small-zh, needs sentence-transformers)
python benchmarks/retrieval_bench.py --real
```

Expected output (mock):
```
Retrieval Quality Benchmark
  seeds: 20 (4 types x 5 time buckets)
  queries: 10 (5 temporal + 5 semantic)
  mode: mock

Results:
  Precision@3:          0.07
  Recall@3:             0.30
  MRR@3:                0.60
  Temporal Precision@1: 0.60
  avg latency:          0.6 ms
  p50 latency:          0.6 ms
  p95 latency:          0.8 ms
```

Expected output (--real):
```
Results:
  Precision@3:          0.12
  Recall@3:             0.61
  MRR@3:                0.87
  Temporal Precision@1: 0.60
  avg latency:          15.0 ms
  p50 latency:          12.8 ms
  p95 latency:          38.0 ms
```

### 7.2 Agent Respond Latency

```bash
python benchmarks/latency_bench.py --runs 30
```

Expected output:
```
Agent Respond Latency Benchmark
  runs: 30
  mode: mock (50ms LLM delay)

Results:
  chitchat:        p50=279.8ms  p95=314.3ms  avg=280.3ms
  fact_recall:     p50=281.9ms  p95=295.5ms  avg=278.9ms
  qa_with_tools:   p50=334.8ms  p95=355.6ms  avg=335.8ms
```

### 7.3 Pub/Sub Throughput

```bash
python benchmarks/throughput_bench.py
```

Expected output:
```
Pub/Sub Throughput Benchmark
  events: 200 per agent count
  agents: 2, 5, 10

Results (2 agents):
  Bus throughput:   1,407 events/sec
  Worker throughput: 1,414 events/sec
  Elapsed:          142.2 ms

Results (5 agents):
  Bus throughput:   36,499 events/sec
  Worker throughput: 146,179 events/sec
  Elapsed:          5.5 ms

Results (10 agents):
  Bus throughput:   20,535 events/sec
  Worker throughput: 184,917 events/sec
  Elapsed:          9.7 ms
```

## 8. Common Failures and Fixes

### 8.1 `ConnectionRefusedError` or `ConnectionError` (Ollama not started)

```
[Error] ... Connection refused ...
```

**Fix**:
```bash
# Windows: ensure Ollama app is running (check system tray)
# Or start via CLI:
ollama serve
```

### 8.2 `Model 'qwen3:8b' not found`

```
openai.NotFoundError: model 'qwen3:8b' not found
```

**Fix**:
```bash
ollama pull qwen3:8b
# Or use a different model — edit .env and change OLLAMA_MODEL_ID
```

### 8.3 `ModuleNotFoundError: No module named 'chromadb'`

```
ModuleNotFoundError: No module named 'chromadb'
```

**Fix**:
```bash
pip install chromadb
# First import will download BGE-small-zh (~130 MB)
```

### 8.4 `sentence-transformers` download failure (China mainland)

BGE-small-zh is hosted on HuggingFace Hub, which may be slow or blocked in mainland China.

**Fix — set HF mirror**:
```bash
$env:HF_ENDPOINT = "https://hf-mirror.com"   # Windows PowerShell
export HF_ENDPOINT="https://hf-mirror.com"    # macOS/Linux
```

### 8.5 ChromaDB `sqlite3` version error (Linux)

```
RuntimeError: Your system has an unsupported version of sqlite3.
Chroma requires sqlite3 >= 3.35.0.
```

**Fix**:
```bash
pip install pysqlite3-binary
# Add to the top of your entry script:
# __import__('pysqlite3').sys.modules['sqlite3'] = __import__('pysqlite3')
```

### 8.6 `UnicodeEncodeError` in Windows console

Chinese characters may not render correctly in Windows CMD or older PowerShell.

**Fix**:
```bash
# Use Windows Terminal (recommended), or set encoding:
chcp 65001
$env:PYTHONIOENCODING = "utf-8"
```

### 8.7 Tests all skip with "Ollama not available"

This is expected behavior for `test_memory_aware_agent.py` — 1 of 14 test files requires Ollama. The remaining 13 files (155+ tests) run fine without it.

### 8.8 `PermissionError` when resetting memory on Windows

```
PermissionError: [WinError 32] The process cannot access the file because
it is being used by another process
```

**Fix**: The SQLite `.db` file is locked by a background `AsyncDispatcher` thread. Wait 2-3 seconds after the last demo run before `--reset`, or manually delete the `.db` file when no Python process is running.

## 9. CI (GitHub Actions)

The CI workflow (`.github/workflows/python-tests.yml`) runs on every push and PR:
- `pip install -r requirements.txt`
- `python -m pytest Test/ -q`

It does NOT run benchmarks (they require manual invocation) and does NOT run real-LLM demos (Ollama is not available in CI).
