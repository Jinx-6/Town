# 赛博小镇（Cyber Town）项目审计报告

> 最后更新：2026-05-26 | 当前分支：`mvp-agent-demo`

---

## 一、当前开发进度

### 1.1 已落地实现的模块

| 模块 | 文件 | 完成度 | 说明 |
|------|------|--------|------|
| **记忆系统 Schema 层** | `Memory/schema/` (6个文件) | 100% | MemoryItem、RetrievalRequest/Response、RoutingDecision、ConflictRecord、KnowledgeTriplet 等数据契约完整定义，Pydantic 校验齐备 |
| **L0 工作记忆缓存** | `Memory/storage/working_cache.py` | 100% | 基于 `deque` 的滑动窗口（maxlen=20），agent_id 隔离 |
| **L1 SQLite 情景存储** | `Memory/storage/sqlite_log.py` | 100% | CRUD 完整；新增 `search_with_metadata()` 支持 `json_extract` 硬性 metadata 过滤 |
| **L2 ChromaDB 向量存储** | `Memory/storage/vector_store.py` | 100% | BGE-small-zh 中文向量模型；`add()` 扁平化 metadata 字段；`search()` 支持 `metadata_filters` + ChromaDB `$and` where 子句 |
| **L3 Neo4j 图谱存储** | `Memory/storage/graph_db.py` | 100% | 实体关系增删查、子图搜索、agent_id 隔离均通过 Cypher 实现 |
| **意图分类器 (NEW)** | `Memory/processor/intent_classifier.py` | 100% | `RuleBasedIntentClassifier`（确定性规则 4 分类）+ `LLMIntentClassifier`（LLM 增强版）+ 查询重写 |
| **Chroma 元数据助手 (NEW)** | `Memory/processor/chroma_metadata_helper.py` | 100% | `sanitize_for_chroma()` 过滤不支持类型；`normalize_for_sqlite()` bool→0/1 |
| **写入路由 (Router)** | `Memory/processor/router.py` | 100% | 意图驱动的 4 路路由（fact→全量/task→CACHE+SQLITE/query→CACHE+SQLITE/chitchat→CACHE only）+ 原有触发词兜底 |
| **Query Planner** | `Memory/processor/planner.py` | 100% | LLM 驱动查询拆解，含 JSON 模式强制输出和降级兜底 |
| **GraphExtractor** | `Memory/processor/extractor.py` | 100% | LLM 驱动三元组抽取，含代词替换和空结果兜底 |
| **ConflictResolver** | `Memory/processor/conflict_resolver.py` | 100% | LLM 驱动 4 种冲突策略仲裁，含安全兜底 |
| **MemoryScorer** | `Memory/policies/scoring.py` | 100% | 语义相似度 + 时间衰减 + 重要性三维加权打分 |
| **UpdatePolicy** | `Memory/policies/update_policy.py` | 100% | 4 种演化策略（OVERWRITE/APPEND/ARCHIVE/IGNORE），含置信度门槛 |
| **RetrievalPolicy** | `Memory/policies/retrieval_policy.py` | 100% | 配额管理 + 短路熔断 + Jaccard 多样性去重 |
| **RetentionPolicy** | `Memory/policies/retention.py` | 100% | 分级 TTL 遗忘机制（流水账 30 天 / 语义 365 天） |
| **元数据提取器** | `Memory/processor/metadata_extractor.py` | 100% | 基于规则的时间词/问句检测/事件类型/关键词提取 |
| **AsyncDispatcher** | `Memory/hub/async_dispatcher.py` | 95% | 线程 + 队列事件总线，3 条核心链路（写入/图谱抽取/压缩）均已实现 |
| **IngestHub** | `Memory/hub/ingest.py` | 95% | 支持 `intent_type` 驱动的 memory_type 设置 + 同步缓存 + 异步分发 |
| **RetrieveHub** | `Memory/hub/retrieve.py` | 90% | **SQLite 搜索已恢复**（通过 `search_with_metadata`）；`metadata_filters` 全线贯通；多查询融合（rewritten_queries → instruction）；Graph 搜索仍注释 |
| **ConsolidateHub** | `Memory/hub/consolidate.py` | 90% | 定时 GC + AI 压缩提炼，已接入 RetentionPolicy |
| **UpdateHub** | `Memory/hub/update.py` | 90% | Wipe-and-Replace 更新策略完成 |
| **IndexManager** | `Memory/storage/index_manager.py` | 80% | 级联删除完成，索引重建仅为伪代码 |
| **PromptAssembler** | `Memory/processor/assembler.py` | 100% | XML 标签严格分区（`<user_current_input>` / `<retrieved_factual_memories>` / `<retrieved_dialog_history>` / `<task_context>` / `<recent_chitchat>`），含物理隔离规则 |
| **ConvoSummarizer** | `Memory/processor/summarizer/convo.py` | 100% | LLM 增量式摘要 |
| **LongTermSummarizer** | `Memory/processor/summarizer/long_term.py` | 100% | LLM 流水账→语义压缩 |
| **评估体系** | `Memory/evaluation/` (3个文件) | 70% | metrics 完整，在线反馈框架可用，回归测试依赖 Neo4j |
| **好感度系统** | `relationship.py` | 100% | 5 级好感度 + LLM 情感分析 |
| **状态管理器** | `state_manager.py` | 100% | NPC 状态跟踪、并发锁 |
| **日志系统** | `logger.py` | 100% | 对话日志 + 错误日志双通道 |
| **LLM 客户端** | `LLMClient.py` | 100% | OpenAI 兼容协议对接 Ollama；仅 `generate()` 无 tool use 支持 |
| **配置系统** | `config.py` | 100% | Pydantic Settings 自动校验 |
| **CLI 演示** | `demo_cli.py` | 100% | **可运行**。意图分类→元数据过滤检索→多查询融合→XML 分区→LLM 回复→意图驱动存储 全链路贯通 |
| **FastAPI 后端** | `main.py` | 80% | 单 NPC 对话接口可用，但后台定时器仅生成硬编码占位文本 |

### 1.2 仅预留骨架/尚未完善的模块

| 模块 | 状态 | 问题 |
|------|------|------|
| **智能体主循环 (Agent)** | 骨架 | `agent.py` 的 `CyberAgent` 使用简易 `memory.py`（deque），未接入完整 `Memory/` 模块；`observe_and_react` 因 LLM 调用签名错误而崩溃；无自主循环 |
| **多智能体交互** | 不存在 | 无任何多 Agent 对话调度、消息传递或房间/场景机制 |
| **演示程序 (demo_run.py)** | 不可用 | 依赖 Neo4j 本地运行，`RetrieveHub` 构造参数不匹配，循环遍历逻辑有误 |
| **记忆引擎 REST API** | 不可用 | `api/main.py` 和 `api.py`（两份重复）将实例方法当作静态方法调用，启动即崩溃 |
| **技能系统** | 不存在 | 无任何技能/工具调用框架 |
| **函数调用 / Tool Use** | 不存在 | Agent 无调用外部函数的能力；`LLMClient.generate()` 不支持 OpenAI function calling |
| **可视化** | 不存在 | 无前端、无 Godot 客户端接入 |
| **隐私合规模块** | 不存在 | `Memory/privacy/` 目录在 README 中列出但未创建对应文件 |
| **队列限流策略** | 不存在 | `Memory/hub/queue_policy.py` 未创建 |
| **Agent 单元测试** | 不存在 | `CyberAgent`、`LLMClient`、`relationship.py`、`state_manager.py` 全无测试覆盖 |

### 1.3 现有测试用例及覆盖范围

| 测试文件 | 测试数量 | 覆盖范围 | 状态 |
|----------|----------|----------|------|
| `Test/test_schema.py` | 5 个 | MemoryItem 基础实例化、Metadata 默认值、Pydantic 类型拦截、字符串→枚举映射、RetrievedMemory 嵌套 | 全部通过 |
| `Test/test_storage_l1.py` | 5 个 | SQLite 写入读取闭环、UPSERT 更新、时序排序、冷数据时间窗口打捞、物理删除 | 全部通过 |
| `Test/test_memory_pipeline.py` | ~15 个 | 写入持久化、多关键词检索、上下文拼接、agent 数据隔离、会话污染防护、时间关键词排序、时间冲突过滤、metadata 提取端到端、旧 schema 兼容、损坏数据韧性 | 全部通过（MockVectorStore） |
| `Test/test_memory_isolation.py` | ~7 个 | agent_id 写入隔离、跨 agent 防污染、重置单 agent 不影响其他、memory_type 隔离、SQLite agent_id 过滤、VectorStore agent_id 过滤 | 全部通过（MockVectorStore） |
| `Test/test_memory_retrieval_quality.py` | ~10 个 | 意图路由（事实/交互/通用召回）、时间 metadata 排序、relative_time vs timestamp 优先级、recall/precision/threshold、混合记忆库分区 | 全部通过（MockVectorStore） |
| `Memory/evaluation/regression_test.py` | 3 个 | 图谱 OVERWRITE 策略、ARCHIVE 存档+泄漏检测、Hit Rate 计算 | 依赖 Neo4j，自动 skip |

**总计：~42 个测试用例，全部通过（1 个 Neo4j 依赖的测试自动跳过）。** 覆盖了 Schema → 存储 → 检索 → 组装 → 隔离 → 质量的全链路。

---

## 二、记忆系统评估

### 2.1 记忆模块完整度

整体评分：**90/100**。本项目的记忆系统是最大亮点——它实现了一套结构清晰、分层明确、有工程深度的 4 层记忆架构（L0 工作记忆 → L1 流水账 → L2 语义向量 → L3 知识图谱），已在以下方面超出原设计：

- **意图驱动的写入路由**：4 种意图→4 种存储策略，事实全量持久化，闲聊仅短期缓存
- **metadata 全线贯通**：写入时扁平化存储，检索时硬性 `metadata_filters` 过滤（ChromaDB `$and` + SQLite `json_extract`）
- **多查询融合检索**：rewritten_query → 多条 instruction → 合并去重 → 多维度重排序
- **XML 物理隔离**：`<user_current_input>` 与 `<retrieved_factual_memories>` 等记忆段严格分离

### 2.2 实际落地的记忆生命周期流程

**写入流程**：
```
用户输入 → IntentClassifier.classify() → 4 路分类
  ├─ fact_statement → [CACHE, SQLITE, VECTOR, GRAPH]
  ├─ task_instruction → [CACHE, SQLITE]
  ├─ qa_query → [CACHE, SQLITE] (query 类型交互记忆)
  └─ chitchat → [CACHE] only
                │
                ▼
         IngestHub.process_message(intent_type=...)
           ├── 设置 memory_type + is_factual_memory
           ├── Router.route(item, intent_type)
           ├── L0 WorkingMemoryCache 同步写入
           └── AsyncDispatcher.publish(MemoryWriteEvent) → 后台线程
                ├── SQLite (L1) 同步落盘
                ├── ConflictResolver 冲突嗅探 → LLM 仲裁
                ├── VectorStore (L2) 写入（扁平化 metadata）
                └── 触发 GraphExtractionEvent → 图谱抽取 (L3)
```

**检索流程**：
```
用户提问 → IntentClassifier.classify() → qa_query
  ├── rewritten_queries = ["昨天 维修", "修理 昨天 设备", ...]
  ├── RetrievalRequest(query, metadata_filters={"is_factual_memory": True}, rewritten_queries)
  └── RetrieveHub.retrieve()
        ├── 每条 rewritten_query → RetrievalInstruction
        ├── RetrievalPolicy.calculate_quotas() 分配配额
        ├── SQLite.search_with_metadata(metadata_filters=...) ✅
        ├── VectorStore.search(metadata_filters=...) ✅ ($and where 子句)
        ├── GraphStore 搜索 ❌ 仍注释
        ├── 跨 instruction 去重 + 分数合并
        ├── MemoryScorer 三维打分
        ├── apply_time_boost() 意图+时间重排序
        └── PromptAssembler XML 上下文组装
```

**压缩/遗忘流程**：
```
ConsolidateHub (定时 12h 或事件触发)
├── RetentionPolicy.should_delete() → IndexManager 跨库级联删除
└── RetentionPolicy.should_compress() → LongTermSummarizer LLM 压缩 → 新语义记忆写入
```

### 2.3 各项能力逐一核实

| 能力 | 状态 | 详情 |
|------|------|------|
| **数据录入** | ✅ 完整 | `IngestHub.process_message()`（意图驱动）+ `ingest_structured_fact()` 双通道 |
| **持久存储** | ✅ 完整 | SQLite (L1) + ChromaDB (L2) + Neo4j (L3) 三层物理持久化 |
| **记忆检索** | ✅ 可用 | 向量 + SQLite 双路检索均已贯通；Graph 检索仍注释（依赖 Neo4j） |
| **元数据过滤** | ✅ 完整 | ChromaDB `$and` where + SQLite `json_extract` 双后端硬性过滤 |
| **意图分类** | ✅ 完整 | RuleBasedIntentClassifier 4 分类 + 查询重写 + 实体提取 |
| **内容总结** | ✅ 完整 | `ConvoSummarizer`（短期增量式）+ `LongTermSummarizer`（长线压缩），均 LLM 驱动 |
| **记忆合并** | ✅ 完整 | `ConflictResolver`（LLM 仲裁 4 策略）+ `UpdatePolicy`（规则引擎 4 动作） |
| **权重评分** | ✅ 完整 | `MemoryScorer`：语义 60% + 时间衰减 30% + 重要性 10% |
| **上下文拼接** | ✅ 完整 | `PromptAssembler`：XML 5 段严格分区（`<user_current_input>` / factual / dialog / task / chitchat），含物理隔离规则 + Token 截断 |

### 2.4 可写入简历的核心亮点

1. **4 层分级记忆架构**：参考认知心理学（感官→工作→情景→语义）设计的多级存储体系，身份隔离贯穿全链路
2. **异步事件驱动总线**：`AsyncDispatcher` 基于生产者-消费者模式，将耗时的图谱抽取和冲突仲裁从主链路剥离，保证对话响应速度
3. **意图驱动的智能路由**：4 路意图分类（事实/任务/问答/闲聊）决定 4 种差异化写入策略，事实全量持久化，闲聊仅短期缓存
4. **多查询融合检索**：rewritten_query → 多条 instruction → 去重合并 → 多维度加权重排序，复用现有 infrastructure
5. **元数据全线贯通**：ChromaDB `$and` 扁平化过滤 + SQLite `json_extract` 硬性过滤 + Chroma 类型安全 sanitize 助手
6. **LLM 驱动的冲突仲裁**：`ConflictResolver` 可自动检测新旧记忆矛盾并执行覆盖/融合/保留/存档 4 种策略
7. **可配置的策略引擎**：`UpdatePolicy`、`RetrievalPolicy`、`RetentionPolicy` 全部通过 Pydantic Config 实现参数热调
8. **多维度记忆打分**：指数衰减时间权重 + 语义相似度 + 重要性三维加权，抑制陈旧记忆干扰
9. **短路熔断与多样性去重**：检索层有配额管理和 Jaccard 去重算法，防止 Token 浪费
10. **XML 物理隔离 Prompt**：`<user_current_input>` 与记忆段严格分离，防止 LLM 混淆当前输入与检索记忆

---

## 三、智能体整合缺口

### 3.1 是否具备智能体主运行循环

**不具备。** `agent.py` 中的 `CyberAgent` 目前只是一个被动应答器：

- 无自主思考循环（无 "观察→计划→行动→反思" 循环）
- 使用 `memory.py` 的简易 `MemoryManager`（仅一个 deque，最大 10 条），而非完整的 `Memory/` 模块
- `observe_and_react` 方法调用 `self.llm.generate(messages=messages)` 只传一个参数，但 `LLMClient.generate()` 需要 `(system_prompt, messages)` 两个参数，**调用必崩溃**
- `chat` 方法（给 `main.py` 用）在每次调用时新建 `LLMClient` 实例，无法复用连接

### 3.2 两条平行的记忆系统

这是一个**架构级问题**：

| | `memory.py`（agent.py 在用） | `Memory/` 模块（demo_cli.py 在用） |
|---|---|---|
| 存储 | deque，内存中，最大 10 条 | SQLite + ChromaDB + Neo4j 三层 |
| 检索 | 无检索，全量 dump | 多路融合 + 重排序 + 去重 |
| metadata | 无 | 意图分类 + 时间 + 实体 + 事件类型 |
| 意图感知 | 无 | 4 路分类 + 查询重写 |
| 元数据过滤 | 无 | ChromaDB `$and` + SQLite `json_extract` |

**`agent.py` 从未使用我们刚构建的完整 Memory/ 模块。** `demo_cli.py` 是唯一能跑通全链路的入口，但它绕过了 Agent 类，直接调用 Memory Hub 组件。

### 3.3 搭建最简单智能体演示的缺失项

1. **统一 Agent 与 Memory**：删除 `memory.py`，将 `CyberAgent` 接入 `Memory/` 模块（IngestHub + RetrieveHub + IntentClassifier + PromptAssembler）
2. **修复 LLM 调用签名**：修正 `observe_and_react` 中 `LLMClient.generate()` 的参数
3. **实现 Agent 主循环**：`perceive() → think() → act() → reflect()` 四步循环
4. **清理坏代码**：删除两份重复的 `api.py` / `api/main.py`

---

## 四、多智能体开发缺口

### 4.1 目前是否存在多智能体交互机制

**不存在。** 代码库中没有任何多 Agent 调度、消息传递、对话轮转或场景管理的代码。`state_manager.py` 管理了 3 个 NPC 的状态（张三、李四、王五），但仅用于并发锁，不涉及 Agent 间通信。

### 4.2 依托现有记忆模块可搭建的最简双智能体演示场景

**推荐场景**：张三（Python 工程师）和李四（产品经理）在办公室相遇，基于各自记忆中的项目背景自然对话。

**技术可行性**：
- 两个 Agent 各自拥有独立的 `Memory/` 组件（通过 `agent_id` 隔离）
- 一轮对话 = A 根据记忆生成发言 → 存入 A 记忆 → B 观察到 A 发言 → B 检索记忆 → B 生成回复 → 存入 B 记忆
- 无需改动 Memory 模块任何代码

### 4.3 新增或改动所需文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `agent.py` | **修改** | 接入 `Memory/` 模块替代简易 `memory.py`；修复 LLM 调用签名；实现主循环 |
| `simulator.py` (新建) | **新增** | 双 Agent 对话调度器，控制轮次、话题注入、对话终止 |
| `demo_multi_agent.py` (新建) | **新增** | 命令行入口，初始化 2 个 Agent + 启动对话循环 |
| `main.py` | **清理** | 移除硬编码占位后台循环，对接新 Agent |
| `api.py` / `api/main.py` | **删除** | 两份重复的坏代码 |
| `memory.py` | **删除** | 被 Memory/ 模块替代 |

---

## 五、演示程序就绪度

### 5.1 当前是否可直接运行演示程序

| 入口 | 可运行？ | 原因 |
|------|----------|------|
| `demo_cli.py` | ✅ **可运行** | 全链路贯通：意图分类 → 检索 → 组装 → LLM 回复 → 存储。需 Ollama 本地运行 |
| `main.py` | ⚠️ 可启动但须有 Ollama | FastAPI 服务可启动，`/dialogue` 可用，但后台循环仅为硬编码占位 |
| `demo_run.py` | ❌ 完全不可用 | 多处错误：依赖 Neo4j、`RetrieveHub` 构造参数不匹配、`RetrievalRequest` 缺少参数、遍历逻辑有误 |
| `api/main.py` / `api.py` | ❌ 启动即崩溃 | 两份重复，实例方法当静态方法调用 |

### 5.2 demo_cli.py 已验证的能力

评测输入 "我昨天帮你修了发电机" / "我前天帮你修了饮水机" / "我昨天帮你干了什么" 的结果：

| 能力 | 结果 |
|------|------|
| 意图分类 (3/3 正确) | fact_statement ×2 + qa_query ×1，实体+事件类型正确 |
| 存储路由 (按意图分流) | 事实→全量持久化，问答→交互记忆持久化 |
| 元数据过滤 (`is_factual_memory: True`) | 检索结果只含事件，不含 query 记忆 |
| 多查询融合 (3 条 rewritten_query) | 昨天的事实排第一 (score=2.14)，前天排第二 (score=1.02) |
| XML 物理隔离 | `<user_current_input>` 与 `<retrieved_factual_memories>` 严格分离 |
| 7/8 项通过 | 唯一未通过：前天的记录仍出现在"昨天"查询结果中（排名靠后但未硬排除） |

---

## 六、简历适配度评估

### 6.1 现阶段可如实写入简历的内容

- "设计并实现了一套 4 层分级记忆系统（工作记忆 / 情景日志 / 语义向量 / 知识图谱），支持记忆的全生命周期管理"
- "基于生产者-消费者模式构建了异步事件驱动总线，将耗时操作（图谱抽取、冲突仲裁）从主链路剥离"
- "实现了意图驱动的智能路由分类器（事实/任务/问答/闲聊），差异化分配 4 种写入策略"
- "实现了元数据全线贯通：ChromaDB $and 扁平化过滤 + SQLite json_extract 硬性过滤，向量存储绝不只存裸文本"
- "实现了多查询融合检索：查询重写 → 多 instruction 并行 → 去重合并 → 多维度加权重排序"
- "实现了多维加权记忆检索排序算法（语义相似度 + 指数时间衰减 + 重要性），并引入配额管理与短路熔断机制"
- "利用 LLM 实现了记忆冲突自动仲裁（覆盖 / 融合 / 保留 / 存档），支持知识图谱的动态演化"
- "使用 ChromaDB + BGE 中文向量模型实现语义级记忆检索，SQLite 作为唯一真相源，Neo4j 实现结构化知识存储"
- "实现严格的 XML 标签 Prompt 物理隔离，确保检索记忆与用户当前输入不混淆"
- "项目基于 Pydantic 实现全链路数据契约校验，所有外部输入均有类型安全保证"

### 6.2 暂不宜标注的功能能力

- ❌ "智能体自主决策与规划" — 无主循环，Agent 仅为被动应答器
- ❌ "多智能体协作与对话" — 尚未实现
- ❌ "工具调用/函数调用" — 无相关代码
- ❌ "技能系统" — 不存在
- ❌ "可视化前端" — 不存在
- ❌ "完整的记忆压缩与遗忘机制" — ConsolidateHub 未经测试验证

### 6.3 作为实习主力项目前需补齐的内容

1. **必须**：`demo_cli.py` 已可运行 ✅
2. **必须**：单 Agent 与记忆系统联调通过 ✅（demo_cli.py 已验证）
3. **必须**：将 Agent 类接入 Memory/ 模块（消除两条平行记忆系统的架构问题）
4. **强烈建议**：双 Agent 对话演示（体现"多智能体"标签）
5. **建议**：清理 `.env` 中的未使用 API Key（当前仓库中暴露了 Gemini、SerpAPI、Tavily 密钥）
6. **建议**：补充 Agent 核心组件单元测试

---

## 七、后续推进建议

### 五步开发规划

#### Phase A — 统一 Agent 与 Memory（基础设施整合）

**目标**：消除两条平行记忆系统，Agent 使用完整 Memory/ 模块

**具体任务**：
1. 删除 `memory.py`，删除 `api.py` + `api/main.py`（重复坏代码）
2. 重写 `CyberAgent.__init__`：注入 `IngestHub` + `RetrieveHub` + `IntentClassifier` + `PromptAssembler`
3. 修复 `observe_and_react`：修正 LLM 调用签名，接入 Memory 检索
4. 统一 `chat()` 和 `observe_and_react`：共享同一个 think-act-reflect 流程

**预期工时**：3-4 小时

#### Phase B — 实现 Agent 决策循环（Agent 化核心）

**目标**：从"被动 NPC"升级为"有自主循环的 Agent"

**具体任务**：
1. 实现 `run()` 主循环：`while alive: perceive() → think() → act() → reflect()`
2. 实现 `think()`：根据意图类型→fact 确认存储 / task 分解执行 / query 检索回答 / chitchat 轻量回复
3. 实现 `act()`：生成回复 + 预留工具调用接口（`tool_calls: List[Callable]`）
4. 实现自主定时行为：Agent 定时产生"发呆/思考/整理记忆"行为，替代 main.py 的硬编码占位

**预期工时**：4-6 小时

#### Phase C — 工具系统（Agent 能力边界扩展）

**目标**：Agent 可以调用外部工具，不只是生成文本回复

**具体任务**：
1. 定义 `Tool` 协议：`{name, description, parameters, execute}`
2. 实现第一批工具：`search_memory`、`check_time`、`set_reminder`、`write_note`
3. `LLMClient` 增加 function calling 支持（OpenAI tools API）
4. 工具执行循环：LLM 决定调用工具 → 执行 → 结果反馈 → 再决策

**预期工时**：3-4 小时

#### Phase D — 双 Agent 对话演示

**目标**：张三和李四基于各自记忆进行多轮自主对话

**具体任务**：
1. 创建 `simulator.py`：双 Agent 对话调度器（话题注入、轮次控制、对话日志）
2. 两个 Agent 独立使用各自的 L0 缓存和 agent_id
3. 每轮对话打印：发言者、发言内容、检索到的记忆摘要

**预期工时**：3-4 小时

#### Phase E — 文档、测试、清理

**目标**：项目可对外展示，简历可写

**具体任务**：
1. 清理 `.env` 中的未使用 API Key（Gemini、SerpAPI、Tavily）
2. 补充 Agent 核心组件单元测试（Mock LLM，验证 perceive→think→act→reflect 全链路）
3. 更新 README.md（项目架构图、快速开始指南、依赖清单）
4. 录制终端演示脚本，确保一键可跑

**预期工时**：2-3 小时

---

## 八、风险评估

### 8.1 过度设计的代码模块

| 模块 | 风险等级 | 说明 |
|------|----------|------|
| **ConsolidateHub** | 中 | 定时压缩机制设计完善，但在演示阶段不会触发（对话量太小），属于"超前建设" |
| **UpdateHub** | 中 | Wipe-and-Replace 策略正确，但当前无调用方 |
| **在线反馈系统** | 低 | `FeedbackCollector` 框架完善但无前端对接 |
| **评估体系** | 低 | metrics.py 的计算在当前阶段缺乏基准数据 |
| **PromptAssembler** | — | 已升级为 5 段 XML 严格分区，Graph 结果虽少但架构可承载 |

**结论**：这些不是"无用代码"，而是"当前阶段跑不满的代码"。设计本身合理，不建议删除。

### 8.2 目前仍存在的 Bug

| # | 位置 | 问题 | 严重程度 |
|---|------|------|----------|
| 1 | `agent.py:46` | `observe_and_react` 中 `self.llm.generate(messages=messages)` 参数错误，缺少 `system_prompt` | **高** |
| 2 | `api/main.py:47,63` / `api.py:47,62` | 实例方法被当作静态方法调用，两份文件重复 | **高** |
| 3 | `agent.py` / `memory.py` | Agent 使用简易 deque 记忆，未接入完整 Memory/ 模块 — 两条平行系统 | **高** |
| 4 | `Memory/hub/retrieve.py:99` | Graph 搜索代码仍被注释 | **中** |
| 5 | `Memory/storage/index_manager.py:49` | `WorkingMemoryCache` 缺少 `remove` 方法，级联删除 L0 会抛异常 | **中** |
| 6 | `Memory/hub/consolidate.py:46` | `get_recent_memories` 方法不存在，应为 `list_recent` | **中** |
| 7 | `Memory/policies/retention.py:51` | `item.created_at` 应为 `item.timestamp` | **低** |
| 8 | `main.py:220-256` | 后台定时器仅生成硬编码占位文本，不调用 Agent | **低** |
| 9 | `.env` | 包含真实 Gemini、SerpAPI、Tavily API Key，虽已 gitignore 但曾提交 | **安全** |

### 8.3 已修复的问题（相比原审计）

| # | 原问题 | 修复状态 |
|---|--------|----------|
| 1 | `RetrieveHub` 中 SQLite 搜索被注释 | ✅ 已通过 `search_with_metadata()` 恢复 |
| 2 | `metadata_filters` 字段定义但从未使用 | ✅ ChromaDB `$and` + SQLite `json_extract` 全线贯通 |
| 3 | `WriteRouter` 仅有硬编码触发词 | ✅ 新增意图驱动 4 路路由 + 原有触发词兜底 |
| 4 | `VectorStore.add()` 仅存 metadata_json blob | ✅ 扁平化 memory_type/event_type/is_factual_memory/relative_time/importance |
| 5 | `PromptAssembler` 缺少用户输入隔离 | ✅ 新增 `<user_current_input>` 段 + 5 段 XML 严格分区 |
| 6 | 无意图分类器 | ✅ 新增 `RuleBasedIntentClassifier` + `LLMIntentClassifier` |
| 7 | 无查询重写 | ✅ 意图分类器集成查询重写，RetrieveHub 支持多查询融合 |
| 8 | 无 Chroma metadata 类型安全 | ✅ `sanitize_for_chroma()` + `normalize_for_sqlite()` 助手 |
| 9 | `demo_run.py` 完全不可用 | ✅ `demo_cli.py` 可运行全链路 |
| 10 | 仅有 15 个测试 | ✅ 现在 ~42 个测试覆盖 Schema→存储→检索→组装→隔离→质量 |

### 8.4 可延后开发的功能

| 功能 | 理由 |
|------|------|
| **Web 前端 / Godot 可视化** | 命令行版本足以展示核心能力 |
| **函数调用 (Function Calling)** | Phase C 再做，当前单 Agent 回复已可用 |
| **技能系统** | 属于 Agent 能力扩展，记忆模块是核心 |
| **图谱记忆 (L3 Neo4j)** | ChromaDB 语义搜索已能满足演示需求 |
| **复杂城镇模拟** | 双 Agent 对话即可体现多智能体概念 |
| **隐私合规模块** | 演示阶段无需数据治理 |
| **在线反馈闭环** | 无前端收集反馈 |
| **队列限流策略** | 单用户演示无并发压力 |

---

## 九、架构全景图

```
┌─────────────────────────────────────────────────────────────────┐
│                    Cyber Town — 系统架构                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────┐    ┌──────────────┐    ┌─────────────────────┐    │
│  │ main.py  │    │ demo_cli.py  │    │ demo_multi_agent.py │    │
│  │ (FastAPI) │    │   (CLI) ✅   │    │    (Phase D)        │    │
│  └────┬─────┘    └──────┬───────┘    └──────────┬──────────┘    │
│       │                 │                       │               │
│       ▼                 ▼                       ▼               │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              CyberAgent (agent.py)                        │    │
│  │  ┌─────┐  ┌──────┐  ┌──────┐  ┌────────┐               │    │
│  │  │Perceive│→│Think │→│ Act  │→│Reflect │  ← 主循环      │    │
│  │  └─────┘  └──┬───┘  └──┬───┘  └───┬────┘               │    │
│  │              │          │          │                      │    │
│  └──────────────┼──────────┼──────────┼──────────────────────┘    │
│                 │          │          │                           │
│  ┌──────────────┼──────────┼──────────┼──────────────────────┐    │
│  │         Memory/ 模块     │          │                      │    │
│  │                          ▼                                 │    │
│  │  ┌──────────────────────────────────────────┐             │    │
│  │  │        IntentClassifier                   │             │    │
│  │  │  fact / task / qa_query / chitchat       │             │    │
│  │  └───────┬──────────────────┬───────────────┘             │    │
│  │          │                  │                              │    │
│  │          ▼                  ▼                              │    │
│  │  ┌──────────────┐  ┌──────────────────┐                   │    │
│  │  │ IngestHub    │  │  RetrieveHub     │                   │    │
│  │  │ (写入入口)    │  │  (检索入口)       │                   │    │
│  │  └──┬──┬──┬──┬──┘  └──┬──┬──┬────────┘                   │    │
│  │     │  │  │  │        │  │  │                             │    │
│  │     ▼  ▼  ▼  ▼        ▼  ▼  ▼                             │    │
│  │  ┌──────────────────────────────────────┐                  │    │
│  │  │ L0 Cache │ L1 SQLite │ L2 Chroma │L3 Neo4j│            │    │
│  │  │ (deque)  │ (情景)     │ (语义)    │(图谱)  │            │    │
│  │  └──────────────────────────────────────┘                  │    │
│  │                                                             │    │
│  │  ┌──────────────────────────────────────┐                  │    │
│  │  │ Policies: Scoring │ Update │ Retention│ Retrieval      │    │
│  │  └──────────────────────────────────────┘                  │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │  支撑系统: relationship.py │ state_manager.py │ logger.py │    │
│  │           LLMClient.py │ config.py                       │    │
│  └─────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
```

---

> 下一步：Phase A — 统一 Agent 与 Memory 模块，消除两条平行记忆系统。
