# Cyber Town — 多智能体记忆驱动对话系统

## 一句话

Cyber Town 是一个多 Agent 对话系统，每个 Agent 拥有独立的 4 层记忆，Agent 之间通过发布订阅 EventBus 自主对话，无需脚本编排。

## 架构

```
Simulator (编排)
  └─ EventBus (发布订阅路由)
       └─ AgentWorker (协议适配)
            └─ MemoryAwareAgent (记忆感知智能体)
                 ├─ classify (意图分类)
                 ├─ retrieve (记忆检索)
                 ├─ assemble (上下文组装)
                 ├─ [tool loop] (工具调用)
                 ├─ generate (LLM 生成)
                 └─ ingest (记忆写入)

Memory 模块 (4 层存储):
  L0 WorkingMemoryCache (deque)  →  热数据
  L1 SQLiteLogStorage            →  情景日志, 全量持久化
  L2 ChromaDB + BGE-small-zh     →  语义向量检索
  L3 Neo4j                       →  知识图谱 (可选, v1 关闭)
```

## 一个 Memory Lifecycle 示例

```
用户说: "我昨天帮你修了发电机"
  │
  ├─ IntentClassifier → fact_statement
  │
  ├─ IngestHub 写入:
  │    L0 Cache: [..., "修了发电机"]
  │    L1 SQLite: INSERT content="修了发电机", metadata={memory_type:"fact", relative_time:"yesterday"}
  │    L2 ChromaDB: embed("修了发电机") → 768d 向量
  │
  ▼ (第二天)
  │
用户问: "你还记得我昨天帮我做了什么吗"
  │
  ├─ IntentClassifier → qa_query, rewritten_queries=["昨天 维修", "修理 昨天"]
  │
  ├─ RetrieveHub 检索:
  │    SQLite: LIKE '%昨天%' + LIKE '%修%' → 命中 "修了发电机"
  │    ChromaDB: 语义相似度排序 → score=2.14
  │
  ├─ PromptAssembler XML 组装:
  │    <retrieved_factual_memories>
  │      - 我昨天帮你修了发电机 (score: 2.14)
  │    </retrieved_factual_memories>
  │
  ├─ LLM: "你昨天帮我修了发电机，非常感谢！"
  │
  └─ IngestHub: 存储这条问答交互
```

## 一个 Pub/Sub 对话示例

```
[System] 注入种子 → town.public: 「大家好，今天天气真不错！」

张三 > 是啊，阳光明媚。我在办公室修了一上午的 bug。

李四 > 产品需求文档我更新好了，下午有空看看吗？

张三 > 好的，下午 3 点我找你。我记得 deadline 是周五对吧？

李四 > 没错，周五之前要完成接口联调。

[System] 对话结束
  张三 (zhang_san): 处理了 3 条消息
  李四 (li_si): 处理了 3 条消息
```

## 三个 Benchmark 数字

| 指标 | 数值 | 条件 |
|------|------|------|
| Agent 响应延迟 (p50) | 280 ms | Mock LLM 50ms, 含检索+组装+写入 |
| Agent 响应延迟 (tool call) | 335 ms | 同上 + 工具调用 |
| Pub/Sub 吞吐量 (2 agents) | 1,400 evt/s | FakeAgent, 无 LLM 无存储 |
| Pub/Sub 吞吐量 (10 agents) | 20,500 evt/s | FakeAgent, 无 LLM 无存储 |

详见 `benchmarks/results.md`。

## Roadmap

- [x] v1.0: Memory + Agent + Pub/Sub + Tool + Skill, 115 tests
- [ ] v1.1: 时间过滤实现, ConsolidateHub 接入, MockLLM 共享模块
- [ ] v2.0: 跨 Agent 协作, 关系建模, Web UI, 隐私合规
