# Cyber Town（赛博小镇）— 具备长期记忆的多智能体模拟运行时

Cyber Town 是一个多智能体模拟运行时，每个智能体拥有独立的 4 层认知记忆系统。
智能体之间通过发布-订阅 EventBus 自主通信（无需脚本编排），并可通过 LLM 驱动
的 TaskCoordinator 协作完成复杂任务。

不同于普通的 RAG 封装，Cyber Town 实现了**完整的记忆生命周期**：
写入 → 检索 → 压缩 → 纠错 → 遗忘。每个组件以 `agent_id` 隔离，确保多智能体
运行时中记忆不会泄露。

**技术栈**：Python / Ollama / ChromaDB + BGE-small-zh / SQLite / pytest（Neo4j 可选）。

**165 个测试，全部通过。**

> English version: [README_EN.md](README_EN.md)

## 核心特性

- **4 层认知记忆**：工作记忆 (L0) → 情景日志 (L1) → 语义向量库 (L2) → 知识图谱 (L3)
- **完整记忆生命周期**：写入 → 检索 → 压缩 → 更新 → 反馈纠错
- **智能体级记忆隔离**：所有存储层均以 `agent_id` 进行数据隔离
- **事件驱动多智能体运行时**：发布-订阅 EventBus + SchedulerPolicy 调度策略
- **关系管理器**：5 级亲密度系统 + LLM 情感分析
- **工具调用 + 技能层**：3 个内置工具，可扩展的技能注册表
- **LLM 编排的任务协作**：任务分解 → 按角色分配 → 独立执行 → 结果合成
- **手动记忆压缩**：ConsolidateHub + RetentionPolicy（无自动定时器）
- **反馈驱动纠错**：FeedbackCollector 桥接用户纠错 → UpdateHub
- **基准测试**：检索质量、智能体延迟、发布订阅吞吐量
- **CI**：GitHub Actions，push/PR 自动运行

## 架构总览

```
┌──────────────────────────────────────────────────────┐
│                  用户 / Demo 脚本                      │
├──────────────────────┬───────────────────────────────┤
│   TaskCoordinator    │         Simulator              │
│   (LLM 任务编排)      │     (Pub/Sub 调度)             │
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
│(对话交互)     │                  │(任务执行)          │
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
│                  记忆运行时                            │
│  L0 WorkingCache  │  L1 SQLite  │  L2 ChromaDB       │
│  Policies: Scoring / Retention / Retrieval / Update  │
│  Hubs: Ingest / Retrieve / Consolidate / Update       │
└──────────────────────────────────────────────────────┘
```

## 记忆生命周期

### 写入 — 意图驱动的路由分发

```
用户输入 → IntentClassifier（4 分类）
  ├─ fact_statement   → [CACHE, SQLITE, VECTOR, GRAPH]
  ├─ task_instruction → [CACHE, SQLITE]
  ├─ qa_query        → [CACHE, SQLITE]
  └─ chitchat        → [CACHE]
  → IngestHub.process_message()
    → L0 Cache（同步写入）
    → AsyncDispatcher → L1 SQLite + L2 ChromaDB 向量嵌入
```

### 检索 — 混合搜索 + 三维打分

```
Query → QueryPlanner → 多查询改写
  → SQLite 关键词 + ChromaDB 语义搜索
  → MemoryScorer: 语义相关度 (60%) + 时间衰减 (30%) + 重要性 (10%)
  → 去重 + 排序 → PromptAssembler XML 分区组装
```

### 压缩 — 手动触发的情景记忆压缩与过期清理

```python
bundle = create_agent_runtime(..., enable_consolidation=True)
bundle.consolidate_once()  # 手动触发一次压缩，无定时器
# RetentionPolicy: 情景记忆压缩为语义记忆，删除过期条目
```

### 更新 — 全量覆写式记忆纠错

```python
bundle.update_memory(memory_id, "修正后的内容")
# IndexManager 级联删除旧数据 → AsyncDispatcher 跨存储重新写入
```

### 反馈 — 用户纠错自动桥接到 UpdateHub

```python
event = FeedbackEvent(type=CORRECTION, cited_memory_ids=[mid],
                      text_comment="修正后的信息")
bundle.record_feedback(event)
# → 写入 JSONL 日志 → UpdateHub.update_content() 自动触发
```

## 多智能体协作

通过 `TaskCoordinator` + `CollabAgentWorker` 实现 LLM 驱动的任务编排：

```
用户请求：「帮我做登录页面的完整方案」
  │
  ▼
TaskCoordinator.decompose_with_llm()     ← LLM 拆分为角色子任务
  ├─ [产品经理] 撰写需求文档
  ├─ [工程师] 评估技术方案
  └─ [设计师] 设计 UI 布局
  │
  ▼
assign_by_role() → EventBus.publish(task_assigned)
  │
  ▼
CollabAgentWorker._handle_task()         ← 各智能体独立执行
  → agent.respond(task.description) → 返回 task_done 事件
  │
  ▼
TaskCoordinator.synthesize_results()     ← LLM 合并为最终方案
```

关键设计决策：
- Coordinator 是运行时组件，**不是智能体** — 无记忆开销
- 智能体**通过 Coordinator 通信**，不直接对话 — 避免无限聊天循环
- `task_assigned` / `task_done` 复用现有 `Event.type` 字段 — 无需协议改动
- `CollabAgentWorker` 继承 `AgentWorker` — 继承所有过滤和关系逻辑

## 快速开始

```bash
git checkout mvp-agent-demo
pip install -r requirements.txt
```

Mock 模式无需 Ollama — 只需要 Python 和上述依赖即可运行。

### 确定性复现脚本（无需 LLM）

```bash
python demo_reproduce.py
```

注入两条带时间戳的种子记忆，查询「昨天」/「前天」并打印完整检索追踪。
无 LLM 依赖，适合验证核心记忆管道。

### 各类 Demo

```bash
# 多智能体对话（mock 模式，无需 Ollama）
python demo_multi_agent.py --mock --turns 5

# 多智能体 LLM 协作（mock 模式，无需 Ollama）
python demo_collab.py --mock

# 单智能体交互式 CLI（需要 Ollama）
python demo_cli.py

# 运行全部测试
python -m pytest Test/ -q
```

### 基准测试

```bash
python benchmarks/retrieval_bench.py           # 关键词检索（mock）
python benchmarks/retrieval_bench.py --real    # ChromaDB + BGE-small-zh
python benchmarks/latency_bench.py --runs 30
python benchmarks/throughput_bench.py
```

详细输出和常见问题见 `docs/demo_runbook.md`。

## 测试

**165 个测试，14 个测试文件，全部通过。** CI 通过 GitHub Actions 在 push/PR 时自动运行。

| 范围 | 文件 | 测试数 |
|------|------|--------|
| 记忆核心 | test_schema, test_storage_l1, test_memory_pipeline, test_memory_isolation, test_memory_retrieval_quality | ~63 |
| 智能体 | test_memory_aware_agent, test_agent_tools | ~40 |
| 发布/订阅 | test_multi_agent_pubsub | 21 |
| 技能 | test_skills | 12 |
| Hub 与运行时 | test_relationship_integration, test_consolidate_integration, test_update_integration, test_feedback_integration | 18 |
| 协作 | test_collab | 14 |
| **合计** | **14 个文件** | **165** |

测试使用 `FakeLLM` 和 `MockVectorStore` — CI 中无需 Ollama 或 ChromaDB。

## 路线图

### 已完成
- [x] 4 层记忆 (L0-L3) 完整生命周期
- [x] EventBus + AgentWorker + Simulator 发布/订阅运行时
- [x] 工具调用 + 技能层
- [x] RelationshipManager（5 级亲密度）
- [x] ConsolidateHub / UpdateHub / FeedbackCollector
- [x] TaskCoordinator + CollabAgentWorker（LLM 协作）
- [x] 基准测试 + CI + 165 测试

### 未来计划
- [ ] 持久化 EventStore（事件日志跨重启保留）
- [ ] Web UI 或 Godot 前端
- [ ] `pyproject.toml` 打包
- [ ] 共享 `MockLLMClient` 测试工具

## 文档

- **[Demo 操作手册](docs/demo_runbook.md)** — 环境配置、mock 和真实 demo 命令、pytest、基准测试、预期输出、常见故障修复
- **[项目审计报告](docs/project_audit_packet.md)** — 架构走查、关键代码路径、测试覆盖、已知风险、简历适配度、最终评分
- **[简历文案](docs/resume_version.md)** — 保守版/美化版简历条目、面试 talking points、避免夸大的注意事项
- **[协作流程](docs/collaboration_flow.md)** — 多智能体协作流程与设计原理
- **[面试笔记](docs/interview_notes.md)** — 30 秒电梯演讲、常见问答、工程权衡

## 局限性

- **LLM 依赖**：真实 demo 需要本地运行 Ollama；mock 模式用于 CI/测试
- **L3 Neo4j**：代码完整但默认禁用（`graph_store=None`）
- **单进程**：EventBus 基于内存，不支持跨机器通信
- **无前端**：仅 CLI，无 Web UI / Godot 客户端
- **无持久事件日志**：EventBus 事件在重启后丢失
