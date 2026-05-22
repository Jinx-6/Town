cyber_town_project/
│
├── main.py                 # 📍 整个系统的唯一入口 (存放 FastAPI 路由、启动项、后台死循环逻辑)
│
├── core/                   # 🧠 核心逻辑层
│   ├── __init__.py
│   ├── agent.py            # 存放 CyberAgent 类 (实体的感知与反应)
│   ├── memory.py           # 存放 MemoryManager 类 (记忆的增删改查与加锁机制)
│   └── simulator.py        # 🎬 存放 TownSimulator 类 (也就是你说的“批量对话生成”，上帝视角逻辑)
│
├── infra/                  # ⚙️ 基础设施层 (与外部通信的组件)
│   ├── __init__.py
│   ├── llm_client.py       # 存放 LLMClient 类 (专职负责调用各类大模型 API)
│   └── database.py         # (预留) 未来放连接 Qdrant/Redis 的代码
│
├── requirements.txt        # 依赖包列表
└── .env                    # (重要) 存放你的环境变量和私密配置

memory.py 实现人物得记忆
config.py 进行参数得导入从.env文件中，他的作用是在运行之前就将所有得参数检查完毕，防止在跑得过程中在报错
logger.py 记录小镇上发生得一切，上帝视角，给到程序员看
state_manager.py 状态管理器负责跟踪每个 NPC 的当前状态，包括位置、是否忙碌、当前动作等。这对于防止并发问题很重要,比如避免一个 NPC 同时与多个玩家对话。
relationship.py 将与用户的关系进行升级改变
agent.py 
LLMClient.py 将llm的api转为适应于本例的样子