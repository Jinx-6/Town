"""
Agent 工厂：创建 MemoryAwareAgent 所需的全部组件并组装。
从 demo_cli.py 提取，供所有入口复用。
"""
import os

from Memory.storage.working_cache import WorkingMemoryCache
from Memory.storage.sqlite_log import SQLiteLogStorage
from Memory.storage.vector_store import VectorStore

from Memory.processor.router import WriteRouter
from Memory.processor.planner import QueryPlanner
from Memory.processor.assembler import PromptAssembler
from Memory.processor.intent_classifier import create_intent_classifier

from Memory.policies.scoring import MemoryScorer
from Memory.policies.retrieval_policy import RetrievalPolicy
from Memory.policies.update_policy import UpdatePolicy

from Memory.hub.async_dispatcher import AsyncDispatcher
from Memory.hub.ingest import IngestHub
from Memory.hub.retrieve import RetrieveHub

from Memory.schema.routing import BackendTarget


def reset_memory(agent_id: str, sqlite: SQLiteLogStorage, vector_store: VectorStore):
    """清除指定 agent 的所有记忆数据。"""
    db_path = f"demo_{agent_id}.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    try:
        vector_store.delete_by_agent(agent_id)
    except Exception:
        pass
    print(f"[System] 已清除 {agent_id} 的全部记忆数据")


def setup_memory(agent_id: str):
    """组装记忆系统：SQLite + ChromaDB，不含 Neo4j。"""
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


def create_memory_aware_agent(
    *,
    agent_id: str,
    agent_name: str,
    agent_role: str,
    llm_client,
    base_system_prompt: str = "",
    time_boost_fn=None,
):
    """一站式创建 MemoryAwareAgent。"""
    ingest, retrieve, cache, sqlite, vector_store = setup_memory(agent_id)

    classifier = create_intent_classifier(use_llm=False)
    assembler = PromptAssembler(agent_name=agent_name)

    from agents.memory_aware_agent import MemoryAwareAgent

    return MemoryAwareAgent(
        agent_id=agent_id,
        agent_name=agent_name,
        agent_role=agent_role,
        ingest_hub=ingest,
        retrieve_hub=retrieve,
        intent_classifier=classifier,
        prompt_assembler=assembler,
        llm_client=llm_client,
        base_system_prompt=base_system_prompt,
        time_boost_fn=time_boost_fn,
    ), cache, sqlite, vector_store
