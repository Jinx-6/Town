"""
单智能体记忆驱动对话演示 (CLI)
无需 Neo4j，基于 SQLite + ChromaDB 运行
"""
import sys
import time
import asyncio
from pathlib import Path

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).parent))

from Memory.schema.memory_item import MemoryRole
from Memory.schema.routing import BackendTarget
from Memory.schema.retrieval import RetrievalRequest

from Memory.storage.working_cache import WorkingMemoryCache
from Memory.storage.sqlite_log import SQLiteLogStorage
from Memory.storage.vector_store import VectorStore

from Memory.processor.router import WriteRouter
from Memory.processor.planner import QueryPlanner
from Memory.policies.scoring import MemoryScorer
from Memory.policies.retrieval_policy import RetrievalPolicy
from Memory.policies.update_policy import UpdatePolicy
from Memory.processor.assembler import PromptAssembler

from Memory.hub.async_dispatcher import AsyncDispatcher
from Memory.hub.ingest import IngestHub
from Memory.hub.retrieve import RetrieveHub

from LLMClient import LLMClient


def setup_memory(agent_id: str):
    """组装记忆系统：SQLite + ChromaDB，不含 Neo4j"""
    cache = WorkingMemoryCache()
    sqlite = SQLiteLogStorage(db_path=f"demo_{agent_id}.db")
    vector_store = VectorStore()

    router = WriteRouter()
    update_policy = UpdatePolicy()

    backends = {
        BackendTarget.SQLITE: sqlite,
        BackendTarget.VECTOR: vector_store,
    }

    dispatcher = AsyncDispatcher(
        backends=backends,
        extractor=None,
        conflict_resolver=None,
        index_manager=None,
        update_policy=update_policy,
    )

    ingest = IngestHub(working_cache=cache, router=router, dispatcher=dispatcher)

    planner = QueryPlanner()
    scorer = MemoryScorer()
    retrieval_policy = RetrievalPolicy()

    retrieve = RetrieveHub(
        planner=planner,
        cache=cache,
        sqlite=sqlite,
        vector_store=vector_store,
        graph_store=None,
        scorer=scorer,
        policy=retrieval_policy,
    )

    return ingest, retrieve, cache, sqlite, vector_store


async def run_demo():
    agent_id = "demo_agent"
    agent_name = "张三"
    agent_role = "Python工程师"

    print("=" * 55)
    print("  赛博小镇 — 单智能体记忆驱动对话演示")
    print("=" * 55)

    # 初始化记忆系统
    print("\n[System] 正在初始化记忆引擎 (SQLite + ChromaDB)...")
    try:
        ingest, retrieve, cache, sqlite, vector_store = setup_memory(agent_id)
        print("[System] 记忆引擎就绪")
    except Exception as e:
        print(f"[Error] 记忆引擎启动失败: {e}")
        import traceback
        traceback.print_exc()
        return

    llm = LLMClient()
    assembler = PromptAssembler(agent_name=agent_name)

    base_system_prompt = (
        f"你是{agent_name}，一位{agent_role}。"
        f"你在 Datawhale 办公室工作。请基于你的记忆与用户自然对话。"
        f"如果记忆中有用户之前告诉过你的信息，请自然地引用。"
    )

    print(f"\n{'=' * 55}")
    print(f"  {agent_name}（{agent_role}）已上线")
    print(f"  输入消息开始对话，输入 /quit 退出")
    print(f"{'=' * 55}\n")

    turn = 0
    while True:
        try:
            user_input = input("You > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[System] 对话结束")
            break

        if not user_input:
            continue
        if user_input in ("/quit", "/exit", "/q"):
            print("[System] 对话结束")
            break

        turn += 1
        print()

        # ─── 1. 先检索历史记忆（本轮输入尚未入库）───
        request = RetrievalRequest(query=user_input, limit=5, agent_id=agent_id)
        results = retrieve.retrieve(request, agent_id=agent_id)

        # ─── 2. 调试：打印检索结果 ───
        print(f"  ┌─ [Debug] 检索到 {len(results.results)} 条记忆")
        for i, mem in enumerate(results.results):
            source = str(mem.source)
            content_preview = mem.item.content[:60].replace("\n", " ")
            print(f"  │  {i+1}. [{source}] score={mem.score:.4f} | {content_preview}...")
        print(f"  └─")

        # ─── 3. 组装上下文 → 生成回复 ───
        context_messages = assembler.assemble(user_input, results)
        full_messages = [{"role": "system", "content": base_system_prompt}] + context_messages

        print(f"\n{agent_name} > ", end="", flush=True)
        try:
            response = await llm.generate(
                system_prompt=base_system_prompt,
                messages=context_messages,
            )
        except Exception as e:
            response = f"(LLM 调用失败: {e})"

        print(response)
        print()

        # ─── 4. 生成回复后，再将本轮对话写入记忆库，供后续轮次使用 ───
        ingest.process_message(
            content=user_input,
            role=MemoryRole.USER,
            agent_id=agent_id,
        )
        time.sleep(0.3)
        ingest.process_message(
            content=response,
            role=MemoryRole.ASSISTANT,
            agent_id=agent_id,
        )
        time.sleep(0.3)


if __name__ == "__main__":
    asyncio.run(run_demo())
