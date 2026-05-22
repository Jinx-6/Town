# @Time    :2026/5/13 09:08
# @Author  :进喜
# @File    :demo_run.py

import time
import os
from dotenv import load_dotenv

# 0. 加载环境变量 (确保 API Key 和端口能读到)
load_dotenv()

from Memory.schema.memory_item import MemoryRole
from Memory.schema.routing import BackendTarget

# 1. 引入组件 (请注意类名与你文件中的定义保持一致)
from Memory.storage.graph_db import GraphStore
from Memory.storage.working_cache import WorkingMemoryCache
from Memory.storage.vector_store import VectorStore
from Memory.storage.sqlite_log import SQLiteLogStorage  # 根据你的截图修改了类名
from Memory.policies.update_policy import UpdatePolicy
from Memory.processor.router import WriteRouter
from Memory.processor.extractor import GraphExtractor
from Memory.policies.scoring import MemoryScorer
from Memory.policies.retrieval_policy import RetrievalPolicy
from Memory.hub.async_dispatcher import AsyncDispatcher
from Memory.hub.ingest import IngestHub
from Memory.hub.retrieve import RetrieveHub
from Memory.schema.retrieval import RetrievalRequest

# 🌟 关键修正：从 processor 导入真正的 Planner 执行器，而不是从 schema 导入数据模型
from Memory.processor.planner import QueryPlanner


def setup_engine():
    """🏭 组装整个记忆引擎"""
    print("🔧 [System] 正在启动底层存储引擎...")
    # 端口已修正为 7687
    graph_store = GraphStore(uri="bolt://localhost:7687", user="neo4j", password="jinxi666")

    # 清空测试库
    with graph_store.driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n")

    cache = WorkingMemoryCache()
    vector_store = VectorStore()
    sqlite_store = SQLiteLogStorage()

    backends = {
        BackendTarget.GRAPH: graph_store,
        BackendTarget.VECTOR: vector_store,
        BackendTarget.SQLITE: sqlite_store
    }

    print("🧠 [System] 正在加载策略与处理器...")
    policy = UpdatePolicy()
    router = WriteRouter()
    extractor = GraphExtractor()

    # 🌟 关键修正：实例化真正的处理器组件
    planner = QueryPlanner()
    scorer = MemoryScorer()
    retrieval_policy = RetrievalPolicy()

    print("⚙️ [System] 正在启动异步事件总线...")
    dispatcher = AsyncDispatcher(
        backends=backends,
        extractor=extractor,
        update_policy=policy
    )

    print("🏢 [System] 正在启动业务中枢...")
    ingest_hub = IngestHub(cache, router, dispatcher)

    # 🌟 关键修正：按照 RetrieveHub 的构造函数要求传参
    retrieve_hub = RetrieveHub(
        cache=cache,
        vector_store=vector_store,
        graph_store=graph_store,
        sqlite=sqlite_store,  # 传入 sqlite 实例
        planner=planner,  # 传入执行器实例
        scorer=scorer,
        policy=retrieval_policy
    )

    return ingest_hub, retrieve_hub, graph_store


def run_simulation():
    # 1. 组装系统
    try:
        ingest, retrieve, graph = setup_engine()
    except Exception as e:
        print(f"❌ [Error] 引擎启动失败: {e}")
        return

    print("\n" + "=" * 50)
    print("🚀 [System] 记忆引擎启动完毕，开始模拟测试！")
    print("=" * 50 + "\n")

    # [场景 1 & 2 与你之前代码一致，此处略...]
    msg1 = "你好，我是进喜。我现在居住在北京，在腾讯上班。"
    print(f"🗣️ [User]: {msg1}")
    ingest.process_message(content=msg1, role=MemoryRole.USER, confidence=1.0)

    print("⏳ [System] 正在等待异步处理...")
    time.sleep(15)

    # [场景 3：检索测试]
    print("\n" + "=" * 50)
    print("🕵️ [System] 开始检索测试...")
    question = "我现在住在哪里？"
    request = RetrievalRequest(query=question, limit=5)

    # 这里会触发 planner.plan -> 产生指令 -> 搜索数据库
    result = retrieve.retrieve(request, agent_id = "jinxi")

    print("\n✅ [Retrieval Results]:")
    if not result.results:
        print("   (未检索到相关记忆)")
    print("\n✅ [Retrieval Results]:")

    # 1. 遍历这个大报告
    found_any = False
    for key, value in result:
        # 2. 我们只关注 key 为 'results' 的那一部分数据
        if key == 'results' and isinstance(value, list):
            for res in value:
                found_any = True
                # ✨ 此时 res 是 RetrievedMemory 对象，拥有 .item 和 .score
                stage_str = getattr(res.item.stage, 'value', res.item.stage)

                # 打印出具体的记忆内容和匹配分数 (score 越小代表越匹配)
                print(f"   - [来源: {stage_str}] (得分: {res.score:.4f})")
                print(f"     内容: {res.item.content}")
                print("-" * 30)

    if not found_any:
        print("   (未检索到相关记忆)")


if __name__ == "__main__":
    run_simulation()