memory/

├── hub/                         # 🧠 核心调度中枢（控制流与高并发防线）

│   ├── ingest.py                # 写入入口（极速同步，接收玩家消息）

│   ├── retrieve.py              # 检索出口（多源数据打捞与融合）

│   ├── update.py                # 冲突更新（纯执行者，不包含策略逻辑）

│   ├── consolidate.py           # ⭐ 长期记忆压缩（清理冗余数据的清道夫）

│   ├── async_dispatcher.py      # 🚀 后台任务分发器（不阻塞聊天的关键）

│   └── queue_policy.py          # 队列限流/优先级/丢弃策略

│

├── processors/                  # ⚙️ 智能加工引擎（调用大模型，耗时重灾区）

│   ├── router.py                # ⭐ 路由分发器（决定数据该存入哪个底层库）

│   ├── planner.py               # ⭐ Query Planner（将复杂问题拆解为多个检索指令）

│   ├── assembler.py             # 🚀 上下文组装车间（Token 截断与排版主编）

│   ├── conflict_resolver.py     # 冲突调解员（检测逻辑矛盾并选择解决策略）

│   ├── summarizer/              # 记忆摘要模块

│   │   ├── convo.py             # 短期对话总结

│   │   └── long_term.py         # 长期记忆深度压缩

│   └── extractor/               # 实体提取模块

│       └── fact_extractor.py    # 结构化事实/偏好提取（喂给知识图谱）

│

├── storage/                     # 💾 物理存储基座（各类数据库的驱动层）

│   ├── working_cache.py         # 🚀 L0：工作记忆缓存（Redis/内存，极速响应）

│   ├── sqlite_log.py            # L1：情景记忆（最完整的对话流水账档案）

│   ├── vector_store.py          # L2：语义记忆（Qdrant/Chroma，负责模糊联想）

│   ├── graph_db.py              # L3：知识图谱（稳定的人物画像与关系网）

│   └── index_manager.py         # ⭐ 索引管家（确保四大存储层的数据一致性）

│

├── policies/                    # 📜 动态策略与法则（配置文件化，随时热更新）

│   ├── retention.py             # 遗忘曲线法则（多久删、怎么删）

│   ├── scoring.py               # ⭐ 排序打分权重（时间优先还是相关度优先？）

│   ├── update_policy.py         # 图谱更新规则（声明式配置）

│   └── retrieval_policy.py      # 多路召回融合策略

│

├── schema/                      # 🧬 数据契约与结构（系统稳定运行的防弹衣）

│   ├── memory_item.py           # 记忆单元实体、生命周期定义

│   ├── routing.py               # 路由决策契约

│   ├── retrieval.py             # 检索请求与返回格式

│   ├── conflict.py              # 冲突记录模型

│   └── events.py                # 全局事件总线（写入事件、压缩触发事件等）

│

├── evaluation/                  # 🛡️ 评估与监控（生产环境的测试仪）

│   ├── metrics.py               # 命中率/幻觉率评估

│   ├── regression_test.py       # 记忆衰退/覆盖回归测试

│   └── online_feedback.py       # 收集在线环境真实表现

│

