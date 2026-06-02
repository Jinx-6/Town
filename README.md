# Cyber Town — 多 Agent 记忆驱动对话系统

基于 Ollama 的多智能体对话系统。每个 Agent 拥有独立 4 层记忆（工作记忆→情景日志→语义向量→知识图谱），通过发布订阅 EventBus 进行对话交互，支持工具调用和技能系统。

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境
cp .env.example .env
# 编辑 .env，设置 OLLAMA_BASE_URL 和 OLLAMA_MODEL_ID

# 3. 确保 Ollama 运行中
ollama pull qwen3:8b

# 4. 单 Agent 交互对话
python demo_cli.py

# 5. 多 Agent 发布订阅对话
python demo_multi_agent.py --topic town.public --turns 10

# 6. Dry-run（无需 Ollama）
python demo_multi_agent.py --mock --turns 5

# 7. 运行测试
pytest -q
```

## 架构

```
┌──────────────────────────────────────────────────────────┐
│                     Simulator                             │
│             编排层：Bus / Worker / Topic                    │
└─────────────────────┬────────────────────────────────────┘
                      │ publish / subscribe
┌─────────────────────▼────────────────────────────────────┐
│                     EventBus                              │
│    事件路由：subscribe / publish / dispatch / dedup        │
│    SchedulerPolicy：配额 + per-agent cap + 去重            │
│    不理解 LLM · 不理解记忆                                │
└─────────────────────┬────────────────────────────────────┘
                      │ Event
┌─────────────────────▼────────────────────────────────────┐
│                   AgentWorker                             │
│    Event → respond() → Event 协议转换                      │
│    自事件过滤 / target_agent_id 定向过滤                   │
└─────────────────────┬────────────────────────────────────┘
                      │ respond()
┌─────────────────────▼────────────────────────────────────┐
│                MemoryAwareAgent                           │
│    classify → retrieve → assemble                         │
│    → [tool loop] → generate → ingest                     │
│    + PromptAssembler XML 6 段分区                         │
│    + ToolRegistry / ToolLoop (可选)                       │
│    + SkillRegistry (可选)                                 │
└─────────────────────┬────────────────────────────────────┘
                      │
┌─────────────────────▼────────────────────────────────────┐
│                  Memory 模块 (4 层)                        │
│  L0 WorkingMemoryCache (deque, maxlen=20)                 │
│  L1 SQLiteLogStorage  (情景日志, 全量持久化)               │
│  L2 ChromaDB          (语义向量, BGE-small-zh)            │
│  L3 Neo4j             (知识图谱, 可选, 默认关闭)           │
│  + IngestHub / RetrieveHub / AsyncDispatcher              │
│  + Policies: Scoring / Update / Retention / Retrieval     │
└──────────────────────────────────────────────────────────┘
```

## Memory Lifecycle

### 写入（Ingest）
```
用户输入 → IntentClassifier.classify()
  ├─ fact_statement  → [CACHE, SQLITE, VECTOR, GRAPH]
  ├─ task_instruction → [CACHE, SQLITE]
  ├─ qa_query        → [CACHE, SQLITE]
  └─ chitchat        → [CACHE]
                      │
                      ▼
              IngestHub.process_message()
                ├─ L0 Cache 同步写入
                └─ AsyncDispatcher → 后台线程
                     ├─ SQLite (L1) 落盘
                     ├─ ChromaDB (L2) 向量嵌入
                     └─ Neo4j (L3) 图谱抽取
```

### 检索（Retrieve）
```
用户查询 → QueryPlanner 计划生成
  → multi-query rewrite
  → RetrievalPolicy 配额分配
  → SQLite.search_with_metadata() + VectorStore.search()
  → MemoryScorer 三维打分 (语义 60% + 时间衰减 30% + 重要性 10%)
  → 去重 + 排序
  → PromptAssembler XML 组装
```

### 遗忘（Retention）
```
ConsolidateHub (定时 12h)
  ├─ RetentionPolicy.should_delete() → IndexManager 跨库删除
  └─ RetentionPolicy.should_compress() → LongTermSummarizer 压缩
```

## Pub/Sub Runtime

### 核心组件

| 组件 | 位置 | 职责 |
|------|------|------|
| Event | `agents/event.py` | 事件数据结构：source/target/topic/type/content |
| EventBus | `agents/event_bus.py` | 订阅/发布/调度/去重/配额 |
| SchedulerPolicy | `agents/event_bus.py` | max_total_events + per_agent_max + 去重 |
| AgentWorker | `agents/agent_worker.py` | Event↔Agent 协议转换，自事件/target 过滤 |
| Simulator | `simulator.py` | 编排：创建 Bus + Workers，注入话题，运行 |

### 事件流

```
System inject → town.public → EventBus.publish()
  → dispatch: 张三.handle_event() → respond() → reply Event
  → dispatch: 李四.handle_event() → respond() → reply Event
  → dispatch: reply → reply → ...  (直到 idle 或 max_total_events)
```

### SchedulerPolicy

- `max_total_events`：硬截断，防止无限对话循环
- `per_agent_max`：限制单个 Agent 产出 Event 数量，防止刷屏
- `_dispatched_ids`：event_id 去重，防止同一事件被重复分发

## Tool vs Skill

| | Tool | Skill |
|---|------|-------|
| 粒度 | 单个可调用函数 | Task 级能力封装 |
| 状态 | 无状态 | 可维护内部 buffer |
| 调用 | LLM 决策 (function calling) | 程序化触发 (applies_to) |
| 示例 | `search_own_memory` | `ObservePublicEventSkill` |
| | `get_current_time` | `SummarizeRecentConversationSkill` |
| | `get_agent_state` | |
| 定义 | `agents/tool.py` | `skills/base.py` |

**Tool** = `{name, description, parameters(JSON Schema), execute}` → `ToolResult`

**Skill** = `applies_to(event) + run(event, agent)` → `SkillResult`

## Demo Scenarios

| 场景 | 命令 | 说明 |
|------|------|------|
| **单 Agent 记忆对话** | `python demo_cli.py` | Intent classify → retrieve → assemble → generate → ingest 全链路 |
| **单 Agent + 调试** | `python demo_cli.py --debug-retrieval` | 打印完整检索追踪（意图→候选→重排→XML 上下文） |
| **单 Agent 自主运行** | `python demo_cli.py --auto 5` | Agent 自主发呆/思考 5 轮 |
| **多 Agent Pub/Sub** | `python demo_multi_agent.py` | 张三 + 李四，town.public 频道，Ollama 驱动 |
| **多 Agent Mock** | `python demo_multi_agent.py --mock --turns 10` | Dry-run，无需 Ollama |
| **多 Agent 重置** | `python demo_multi_agent.py --reset` | 清除 SQLite 记忆后启动 |
| **多 Agent 自定义** | `python demo_multi_agent.py --topic room.1 --seed "聊聊项目"` | 自定义频道和种子消息 |

### Run all demos

```bash
# 全部 demo 场景一键验证（都是 --mock，无需 Ollama）
python demo_multi_agent.py --mock --turns 5            # pubsub
python demo_multi_agent.py --mock --turns 5 --reset    # memory_isolation
python demo_cli.py --auto 3                            # temporal_memory (需 Ollama)
# skill_observation 和 tool_calling 由 pytest 覆盖
```

## Tests

```bash
pytest -q                                          # 115 tests, ~4 min
pytest Test/test_multi_agent_pubsub.py -q           # 21 tests, pub/sub
pytest Test/test_skills.py -q                       # 12 tests, skill layer
pytest Test/test_agent_tools.py -q                  # 24 tests, tools
pytest Test/test_memory_pipeline.py -q              # ~20 tests, memory pipeline
pytest Test/test_memory_isolation.py -q             # 8 tests, isolation
pytest Test/test_memory_retrieval_quality.py -q     # ~15 tests, retrieval
```

| 测试文件 | 覆盖 |
|----------|------|
| `test_schema.py` | MemoryItem / Metadata / RetrievedMemory 数据契约 |
| `test_storage_l1.py` | SQLite CRUD / UPSERT / 时间排序 / 冷数据 |
| `test_memory_pipeline.py` | 写入持久化 / 检索 / 组装 / agent 隔离 / 时间排序 / 兼容 |
| `test_memory_isolation.py` | agent_id 写入隔离 / 跨 agent 防污染 / 存储层隔离 |
| `test_memory_retrieval_quality.py` | 意图路由 / 时间 metadata 排序 / 召回精度 / 分区 |
| `test_agent_tools.py` | Tool / ToolRegistry / ToolLoop / PromptAssembler tool 段 |
| `test_memory_aware_agent.py` | Agent 集成（需 Ollama，默认跳过） |
| `test_multi_agent_pubsub.py` | EventBus / AgentWorker / Simulator / memory isolation |
| `test_skills.py` | SkillRegistry / ObservePublicEvent / Summarize / 隔离 |

## 目录结构

```
Town/
├── demo_cli.py              # 单 Agent CLI 入口
├── demo_multi_agent.py      # 多 Agent Pub/Sub CLI 入口
├── simulator.py             # Simulator 编排层
├── LLMClient.py             # OpenAI 兼容协议 → Ollama
├── config.py                # Pydantic Settings
├── relationship.py          # 5 级好感度系统
├── state_manager.py         # NPC 状态管理
├── logger.py                # 对话日志
├── agents/                  # Agent 系统
│   ├── event.py             #   Event 数据结构
│   ├── event_bus.py         #   EventBus + SchedulerPolicy
│   ├── agent_worker.py      #   AgentWorker 协议适配
│   ├── memory_aware_agent.py#   MemoryAwareAgent
│   ├── agent_factory.py     #   Agent 工厂
│   ├── tool.py              #   Tool 协议 + 3 个内置工具
│   ├── tool_registry.py     #   ToolRegistry
│   └── tool_loop.py         #   ToolLoop
├── skills/                  # Skill 系统
│   ├── base.py              #   Skill + SkillResult
│   ├── registry.py          #   SkillRegistry
│   └── builtin.py           #   ObservePublicEvent + SummarizeRecentConversation
├── Memory/                  # 记忆系统 (核心)
│   ├── schema/              #   Pydantic 数据契约
│   ├── storage/             #   L0 Cache + L1 SQLite + L2 ChromaDB + L3 Neo4j
│   ├── processor/           #   Router / Planner / Classifier / Assembler / Extractor
│   ├── hub/                 #   IngestHub / RetrieveHub / AsyncDispatcher
│   ├── policies/            #   Scoring / Update / Retention / Retrieval
│   └── evaluation/          #   Metrics / Regression / OnlineFeedback
├── Test/                    # 115 tests
├── docs/
│   └── runtime_layers.md    # 能力边界定义
├── requirements.txt
├── .env.example
└── .gitignore
```

## Limitations

- **LLM 依赖**：Agent 集成测试和真实 demo 需要 Ollama 本地运行；纯逻辑测试使用 Mock 无需 LLM
- **L3 Neo4j**：代码完整但默认关闭（`graph_store=None`），检索代码被注释
- **Tool Calling**：需要 Ollama 模型支持 OpenAI function calling
- **单进程**：EventBus 是内存实现，不支持跨进程或多机通信
- **无前端**：仅命令行交互，无 Web UI / Godot 客户端
- **无持久事件日志**：EventBus 事件仅内存保留，重启即丢失

## Roadmap

### v1.0 ✅ (当前)

- [x] 4 层 Memory 系统（L0 Cache / L1 SQLite / L2 ChromaDB / L3 Neo4j）
- [x] MemoryAwareAgent（classify → retrieve → assemble → generate → ingest）
- [x] Pub/Sub Multi-Agent Runtime（EventBus + AgentWorker + Simulator）
- [x] Tool System（3 个内置工具 + ToolRegistry + ToolLoop）
- [x] Skill Layer（ObservePublicEvent + SummarizeRecentConversation）
- [x] 115 tests，全绿

### v1.1 (计划)

- [ ] `_apply_time_filter` 时间过滤实现
- [ ] ConsolidateHub / UpdateHub 接入 Agent 循环
- [ ] MockLLMClient 提取为共享测试模块
- [ ] `state_manager.py` / `relationship.py` 单元测试

### v2.0 (远期)

- [ ] 跨 Agent 协作任务（任务分解 / 子任务分配）
- [ ] Agent 间关系建模（好感度 + 信任度）
- [ ] 持久事件日志（EventStore）
- [ ] Web UI 或 Godot 前端接入
- [ ] 隐私合规模块（`Memory/privacy/`）
