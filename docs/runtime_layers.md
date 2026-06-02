# Runtime Layers — 能力边界定义

## 分层架构

```
┌─────────────────────────────────────────────┐
│                  Simulator                   │
│  编排层：创建 Bus / Worker / Topic 注入       │
└──────────────────┬──────────────────────────┘
                   │ publish / subscribe
┌──────────────────▼──────────────────────────┐
│                 EventBus                     │
│  事件发布、订阅、调度                          │
│  ─ 不理解 LLM                                │
│  ─ 不理解记忆                                │
│  ─ 不理解 Agent 内部状态                      │
└──────────────────┬──────────────────────────┘
                   │ Event
┌──────────────────▼──────────────────────────┐
│               AgentWorker                    │
│  协议转换                                    │
│  ─ Event → agent.respond(content)           │
│  ─ AgentResponse → Event                    │
│  ─ 自事件过滤 (source_agent_id)              │
│  ─ 定向过滤 (target_agent_id)                │
└──────────────────┬──────────────────────────┘
                   │ respond()
┌──────────────────▼──────────────────────────┐
│            MemoryAwareAgent                  │
│  记忆感知智能体                               │
│  classify → retrieve → assemble             │
│  → [tool loop] → generate → ingest          │
│  ─ 拥有完整的 Memory/ 模块                    │
│  ─ agent_id 隔离                             │
│  ─ 意图驱动路由                               │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│              Tool / Skill                    │
│                                              │
│  Tool: 单个可调用函数                          │
│    {name, description, parameters, execute}   │
│    例: search_own_memory, get_current_time   │
│                                              │
│  Skill: 面向任务的能力封装                      │
│    可使用一个或多个 Tool                        │
│    例: "查找信息" = search_memory + summarize  │
└─────────────────────────────────────────────┘
```

## 各层职责

### EventBus

| 负责 | 不负责 |
|------|--------|
| 事件发布 (publish) | 理解自然语言 |
| 主题订阅 (subscribe) | 调用 LLM |
| 事件调度 (dispatch_next) | 读写记忆 |
| 去重 (SchedulerPolicy) | 知道 Agent 是谁 |
| 配额控制 (max_total_events, per_agent_max) | 转换数据格式 |
| 历史记录 (history) | 执行业务逻辑 |

**边界**：EventBus 是一个纯消息路由层。它接受 Event 入队，按 topic 匹配 subscribers，调用 handlers，将 handlers 返回的 Event 再次入队。仅此而已。

### AgentWorker

| 负责 | 不负责 |
|------|--------|
| Event → respond() 输入转换 | 理解消息内容 |
| AgentResponse → Event 输出转换 | 做出决策 |
| 自事件过滤 (source_agent_id) | 管理 Agent 生命周期 |
| 定向过滤 (target_agent_id) | 管理 Topic 订阅 |

**边界**：AgentWorker 是一个协议适配器。它不关心消息内容是什么，只关心"这条 Event 该不该让我的 Agent 处理"，以及"Agent 的输出怎样包装成 Event 发出去"。

### MemoryAwareAgent

| 负责 | 不负责 |
|------|--------|
| 意图分类 (classify) | 理解 EventBus 协议 |
| 记忆检索 (retrieve) | 订阅 Topic |
| 上下文组装 (assemble) | 发布 Event |
| 工具调用循环 (tool loop) | 网络传输 |
| LLM 生成 (generate) | 多 Agent 调度 |
| 记忆写入 (ingest) | 去重 / 配额 |

**边界**：MemoryAwareAgent 是一个完整的智能体。它只关心"收到一段文本，基于记忆给出回复"。它不知道 EventBus 的存在，也不知道自己在和谁对话。

### Tool / Skill

**Tool** — 原子能力单元：

```
Tool = {
    name: str              # 唯一名称
    description: str       # LLM 可读的功能说明
    parameters: dict       # JSON Schema
    execute: Callable      # 返回 ToolResult
}
```

- Tool 是同步的、无状态的、单个函数
- Tool 不调用 LLM，不写入记忆（search_own_memory 是只读检索）
- 例：`search_own_memory`, `get_current_time`, `get_agent_state`

**Skill** — 复合能力封装：

```
Skill = 面向任务的编排单元
  ├── Tool 1
  ├── Tool 2
  ├── 执行顺序 / 条件逻辑
  └── 结果聚合
```

- Skill 可以使用一个或多个 Tool，也可以直接调用 LLM、读写 Memory
- 当前已实现：`ObservePublicEventSkill`（观察 → 写入私有记忆）、`SummarizeRecentConversationSkill`（收集事件 → LLM 摘要 → 写入长期记忆）
- Skill 通过 `SkillRegistry.run_if_applicable(event, agent)` 程序化触发，不依赖 LLM function calling

### Memory 模块

| 负责 | 不负责 |
|------|--------|
| 4 层存储 (L0/L1/L2/L3) | 理解 EventBus |
| 意图驱动的写入路由 | 管理 Agent 生命周期 |
| 多维加权检索 + 去重 | 发布/订阅 |
| XML 物理隔离 Prompt 组装 | 多 Agent 调度 |
| 异步事件驱动写入 | 网络通信 |
| 分级 TTL 遗忘 / LLM 压缩 | UI 渲染 |

**边界**：Memory 模块是一个独立的存储引擎。它接收文本 → 分类意图 → 路由到不同存储层 → 检索时多路融合 → 组装 XML Prompt。通过 `agent_id` 实现全链路隔离。
