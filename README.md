Town/
│
├── demo_cli.py              # 主入口 — 单智能体记忆驱动对话 (python demo_cli.py)
├── main.py                  # FastAPI 后端 (待重写，当前不可用)
├── LLMClient.py             # LLM 客户端 — OpenAI 兼容协议对接 Ollama
├── config.py                # Pydantic Settings 配置加载
├── relationship.py          # 5 级好感度系统
├── state_manager.py         # NPC 状态管理
├── logger.py                # 对话日志
│
├── agents/                  # 智能体
│   └── memory_aware_agent.py  # MemoryAwareAgent — 记忆感知单智能体
│
├── Memory/                  # 记忆系统 (核心模块)
│   ├── schema/              # Pydantic 数据契约 (6 文件)
│   ├── storage/             # L0 缓存 + L1 SQLite + L2 ChromaDB + L3 Neo4j
│   ├── processor/           # Router / Planner / Assembler / IntentClassifier
│   ├── hub/                 # IngestHub / RetrieveHub / AsyncDispatcher
│   └── policies/            # Scoring / Update / Retention / Retrieval
│
├── Test/                    # 测试 (70 passed, 1 skipped)
│   ├── test_schema.py
│   ├── test_storage_l1.py
│   ├── test_memory_pipeline.py
│   ├── test_memory_isolation.py
│   ├── test_memory_retrieval_quality.py
│   └── test_memory_aware_agent.py
│
├── MutilAgentjinxi.md       # 项目审计 + 分阶段计划
└── requirements.txt         # 依赖列表

memory.py 实现人物得记忆
config.py 进行参数得导入从.env文件中，他的作用是在运行之前就将所有得参数检查完毕，防止在跑得过程中在报错
logger.py 记录小镇上发生得一切，上帝视角，给到程序员看
state_manager.py 状态管理器负责跟踪每个 NPC 的当前状态，包括位置、是否忙碌、当前动作等。这对于防止并发问题很重要,比如避免一个 NPC 同时与多个玩家对话。
relationship.py 将与用户的关系进行升级改变
agent.py 
LLMClient.py 将llm的api转为适应于本例的样子