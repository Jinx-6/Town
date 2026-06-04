import uuid
from typing import List, Dict, Any, Optional, TYPE_CHECKING
from datetime import datetime, timezone, timedelta

from ..schema.memory_item import MemoryItem, MemoryRole, MemoryStage
from ..schema.routing import BackendTarget
from ..schema.retrieval import RetrievalRequest, RetrievedMemory, RetrievalResponse
from ..storage.working_cache import WorkingMemoryCache
from ..storage.sqlite_log import SQLiteLogStorage
from ..storage.vector_store import VectorStore
from ..processor.planner import QueryPlanner, RetrievalPlan, RetrievalInstruction
from ..policies.scoring import MemoryScorer
# 引入宏观检索策略
from ..policies.retrieval_policy import RetrievalPolicy

if TYPE_CHECKING:
    from ..storage.graph_db import GraphStore


class RetrieveHub:
    """
    全域异构检索中枢 (Strategic Hybrid RAG)
    在 Scoring(微观打分) 基础上，引入 RetrievalPolicy(宏观调控)，
    实现配额管理、短路熔断和多样性去重。
    """

    def __init__(
            self,
            planner: QueryPlanner,
            cache: WorkingMemoryCache,
            sqlite: SQLiteLogStorage,
            vector_store: VectorStore,
            graph_store: Optional["GraphStore"],
            scorer: MemoryScorer,
            policy: RetrievalPolicy
    ):
        self.planner = planner
        self.cache = cache
        self.sqlite = sqlite
        self.vector_store = vector_store
        self.graph_store = graph_store
        self.scorer = scorer
        self.policy = policy

    def retrieve(self, request: RetrievalRequest, agent_id: str) -> RetrievalResponse:
        # 多查询融合：若 rewritten_queries 非空，直接映射为指令，跳过 QueryPlanner
        if request.rewritten_queries:
            instructions = [
                RetrievalInstruction(
                    intent="重写查询融合",
                    search_query=q,
                    keywords=[]
                )
                for q in request.rewritten_queries
            ]
        else:
            plan: RetrievalPlan = self.planner.generate_plan(request.query)
            instructions = plan.instructions

        master_results: Dict[str, RetrievedMemory] = {}

        # 获取配额
        quotas = self.policy.calculate_quotas(request.limit)

        for instruction in instructions:
            sub_results = self._execute_instruction(instruction, request, quotas, agent_id)

            for res in sub_results:
                mem_id = res.item.id
                if mem_id not in master_results:
                    master_results[mem_id] = res
                else:
                    master_results[mem_id].score += (res.score * 0.5)

            # 短路熔断检测
            if self.policy.should_short_circuit(list(master_results.values())):
                print(f"⚡ [RetrieveHub] 触发短路熔断：{agent_id} 已获取高置信度事实。")
                break

        # 全局重排
        sorted_list = sorted(master_results.values(), key=lambda x: x.score, reverse=True)

        # 多样性去重
        diverse_list = self.policy.enforce_diversity(sorted_list)

        return RetrievalResponse(
            original_query=request.query,
            results=diverse_list[:request.limit]
        )

    def _execute_instruction(
            self,
            ins: RetrievalInstruction,
            request: RetrievalRequest,
            quotas: Dict[BackendTarget, int],
            agent_id: str  # ✨ 修改点 3：子方法接收 agent_id
    ) -> List[RetrievedMemory]:
        """
        执行具体的检索指令，并受控于配额(Quotas)与身份隔离
        """
        local_results: Dict[str, RetrievedMemory] = {}
        allowed_targets = request.targets

        # --- A. 检索 L3 知识图谱 ---
        if BackendTarget.GRAPH in allowed_targets and self.graph_store:
            graph_limit = quotas.get(BackendTarget.GRAPH, 5)
            graph_hits = self.graph_store.search_subgraph(
                ins.search_query, agent_id=agent_id, depth=2
            )
            for g_hit in graph_hits[:graph_limit]:
                content = f"{g_hit.get('subject','')} {g_hit.get('predicate','')} {g_hit.get('object','')}"
                fake_item = MemoryItem(
                    id=f"graph_{uuid.uuid4().hex[:8]}",
                    content=f"[知识图谱] {content}",
                    role=MemoryRole.SYSTEM,
                    agent_id=agent_id,
                    stage=MemoryStage.SEMANTIC,
                )
                self._upsert_sub_result(local_results, fake_item, 0.8, BackendTarget.GRAPH)

        # --- B. 检索 L1 SQLite 情景记忆 ---
        if self.sqlite and BackendTarget.SQLITE in allowed_targets:
            sqlite_limit = quotas.get(BackendTarget.SQLITE, 2)
            mf = request.metadata_filters
            logs = self.sqlite.search_with_metadata(
                query=ins.search_query, agent_id=agent_id, limit=sqlite_limit,
                metadata_filters=mf
            )
            for log_item in logs:
                self._upsert_sub_result(local_results, log_item, 0.6, BackendTarget.SQLITE)

        # --- C. 检索 L2 向量库 (语义泛化) ---
        if BackendTarget.VECTOR in allowed_targets:
            vector_limit = quotas.get(BackendTarget.VECTOR, 5)

            # ✨ 修改点 6：核心！向向量库发送带 agent_id 过滤的请求
            # 这里的 agent_id 会被传递给 ChromaDB 的 where {"agent_id": agent_id}
            vec_hits = self.vector_store.search(
                query=ins.search_query,
                limit=vector_limit,
                agent_id=agent_id,
                metadata_filters=request.metadata_filters
            )

            for v_res in vec_hits:
                # 检查召回回来的 MemoryItem，双重保险：确保 agent_id 匹配
                if v_res.item.agent_id != agent_id:
                    continue

                norm_semantic_score = max(0.1, 1.0 - (v_res.score / 1.5))
                final_score = self.scorer.compute_final_score(
                    semantic_score=norm_semantic_score,
                    created_at=v_res.item.timestamp,
                    is_semantic_stage=(v_res.item.stage == MemoryStage.SEMANTIC)
                )

                if final_score >= request.score_threshold:
                    self._upsert_sub_result(local_results, v_res.item, final_score, BackendTarget.VECTOR)

        return self._apply_time_filter(list(local_results.values()), ins.time_filter)

    def _upsert_sub_result(self, collection: Dict[str, RetrievedMemory], item: MemoryItem, score: float,
                           source: BackendTarget):
        if item.id not in collection:
            collection[item.id] = RetrievedMemory(item=item, score=score, source=source)
        else:
            collection[item.id].score = max(collection[item.id].score, score) + 0.1

    def _apply_time_filter(self, results: List[RetrievedMemory], time_filter: str) -> List[RetrievedMemory]:
        if not time_filter or time_filter == "all":
            return results

        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        if time_filter == "today":
            return [r for r in results if r.item.timestamp >= today_start]

        if time_filter == "yesterday":
            yesterday_start = today_start - timedelta(days=1)
            yesterday_end = today_start
            return [
                r for r in results
                if yesterday_start <= r.item.timestamp < yesterday_end
            ]

        return results