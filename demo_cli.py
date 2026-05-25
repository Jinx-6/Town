"""
单智能体记忆驱动对话演示 (CLI)
无需 Neo4j，基于 SQLite + ChromaDB 运行

用法：
  python demo_cli.py                      正常启动
  python demo_cli.py --reset              重置记忆库后启动
  python demo_cli.py --debug-retrieval    打印完整检索追踪日志
  python demo_cli.py --reset --debug-retrieval   重置 + 调试
"""
import sys
import time
import os
import asyncio
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).parent))

from Memory.schema.memory_item import MemoryRole
from Memory.schema.routing import BackendTarget
from Memory.schema.retrieval import RetrievalRequest, RetrievedMemory, RetrievalResponse

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

from Memory.processor.intent_classifier import create_intent_classifier

from LLMClient import LLMClient


# ── 时间关键词 → 时间窗口映射（时间戳层面）────────────────
#   昨天 ≈ 12h~36h 前   前天 ≈ 36h~60h 前   最近 ≈ 0h~6h 前
_TIME_KEYWORD_WINDOWS = [
    (re.compile(r"昨天"), timedelta(hours=12), timedelta(hours=36)),
    (re.compile(r"前天"), timedelta(hours=36), timedelta(hours=60)),
    (re.compile(r"最近|刚刚|刚才"), timedelta(hours=0), timedelta(hours=6)),
]

# 时间意图 → 冲突关键词（内容层面：事件时间冲突检测）
_TIME_CONFLICT_KW: dict = {
    "昨天": {"前天"},
    "前天": {"昨天"},
    "最近": {"昨天", "前天"},
}

_TIME_BOOST = 0.50              # 时间匹配加分
_TIME_CONFLICT_PENALTY = 0.60      # 时间冲突扣分
_INTENT_TYPE_BOOST = 0.30          # 查询意图→记忆类型匹配加分
_INTENT_TYPE_PENALTY = 0.80        # 查询意图→记忆类型不匹配扣分（仅事实召回时对 query 扣分）


def detect_query_intent(query: str) -> str:
    """检测用户的查询意图类型"""
    # 事实事件召回：用户想知道「发生过什么」
    _FACT_RECALL_PATTERNS = [
        re.compile(r"我.*(?:做了?什么|发生了什么|做过|帮.*做了?)"),
        re.compile(r"你(?:还记得|记得).*我.*(?:帮|做|修|买)"),
        re.compile(r"(?:帮我|我.*帮|帮).*(?:做了?什么|吗)"),
    ]
    for p in _FACT_RECALL_PATTERNS:
        if p.search(query):
            return "factual_event_recall"

    # 交互历史召回：用户想知道「我之前问过什么」
    _INTERACTION_PATTERNS = [
        re.compile(r"我.*(?:之前|以前|上次).*(?:问|聊|说过?|提过)"),
        re.compile(r"(?:问过|聊过).*什么"),
        re.compile(r"类似.*问题|问.*类似"),
    ]
    for p in _INTERACTION_PATTERNS:
        if p.search(query):
            return "interaction_history_recall"

    # 任务状态召回：用户想知道「进度/下一步」
    _TASK_PATTERNS = [
        re.compile(r"(?:下一步|进度|做到哪|接下来|还有什么)"),
    ]
    for p in _TASK_PATTERNS:
        if p.search(query):
            return "task_state_recall"

    return "general_recall"


def _get_temporal_intent(query: str):
    """从查询中提取时间意图"""
    for kw in ["前天", "昨天", "最近"]:
        if kw in query:
            return kw
    return None


def _is_noise(content: str) -> bool:
    """检测是否为问句/噪音（不应作为事实记忆）
    规则：以 ？/?/什么/吗 结尾，或包含「你还记得」"""
    c = content.strip()
    if c.endswith("?") or c.endswith("？"):
        return True
    if c.endswith("什么") or c.endswith("吗"):
        return True
    if "你还记得" in c:
        return True
    return False


def apply_time_boost(query: str, results: list) -> list:
    """意图感知重排序：查询意图 → 记忆类型匹配 → 时间冲突 → 时间戳"""
    query_intent = detect_query_intent(query)
    temporal_intent = _get_temporal_intent(query)
    conflict_kws = _TIME_CONFLICT_KW.get(temporal_intent, set())

    # 时间戳窗口匹配
    matched_window = None
    for pattern, start_delta, end_delta in _TIME_KEYWORD_WINDOWS:
        if pattern.search(query):
            matched_window = (start_delta, end_delta)
            break

    now = datetime.now(timezone.utc)
    for r in results:
        meta = r.item.metadata
        content = r.item.content
        mt = getattr(meta, "memory_type", "unknown")
        is_fact = getattr(meta, "is_factual_memory", True)

        # ── 1. 意图 → 类型匹配 ──────────────────────────
        # 对于无元数据的旧记忆 (mt="unknown")，用文本规则兜底
        _effective_mt = mt
        if mt == "unknown":
            if _is_noise(content):
                _effective_mt = "query"
            else:
                _effective_mt = "event"

        if query_intent == "factual_event_recall":
            if _effective_mt == "query" or not is_fact:
                r.score -= _INTENT_TYPE_PENALTY
            elif _effective_mt in ("event", "fact", "preference"):
                r.score += _INTENT_TYPE_BOOST

        elif query_intent == "interaction_history_recall":
            if _effective_mt == "query":
                r.score += _INTENT_TYPE_BOOST
            elif _effective_mt in ("event", "fact", "preference"):
                r.score -= 0.15  # 轻微降权，但保留

        elif query_intent == "task_state_recall":
            if _effective_mt in ("task", "query", "dialogue"):
                r.score += _INTENT_TYPE_BOOST

        # general_recall: 不调整权重，保持原始分数

        # ── 2. 时间冲突检测 ──────────────────────────────
        rel_time = getattr(meta, "relative_time", "")
        if temporal_intent and rel_time and rel_time in conflict_kws:
            r.score -= _TIME_CONFLICT_PENALTY
        elif temporal_intent and conflict_kws and not rel_time:
            for kw in conflict_kws:
                if kw in content:
                    r.score -= _TIME_CONFLICT_PENALTY
                    break

        # ── 3. 时间匹配加分 ──────────────────────────────
        if temporal_intent and rel_time == temporal_intent:
            r.score += _TIME_BOOST
        elif matched_window:
            ts = r.item.timestamp
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            start_delta, end_delta = matched_window
            if now - end_delta <= ts <= now - start_delta:
                r.score += _TIME_BOOST

    results.sort(key=lambda x: x.score, reverse=True)
    return results


def _detect_time_keywords(query: str) -> list:
    """检测查询中包含的时间关键词"""
    found = []
    for pattern, _, _ in _TIME_KEYWORD_WINDOWS:
        match = pattern.search(query)
        if match:
            found.append(match.group())
    return found


def print_debug_retrieval(query: str, results_pre_boost: list, results_post_boost: list,
                          assembler_context: list = None):
    """完整的检索追踪日志"""
    sep = "=" * 66
    print(f"\n{sep}")
    print(f"  [DEBUG-RETRIEVAL] 完整检索追踪")
    print(f"{sep}")

    # 1. 原始查询
    print(f"\n  --- 1. 原始查询 ---")
    print(f"     「{query}」")

    # 2. 查询意图 + 时间意图
    time_kw = _detect_time_keywords(query)
    temporal_intent = _get_temporal_intent(query)
    conflict_kws = _TIME_CONFLICT_KW.get(temporal_intent, set())
    query_intent = detect_query_intent(query)
    print(f"  --- 2. 查询意图检测 ---")
    print(f"     query_intent  = {query_intent}")
    if time_kw:
        print(f"     temporal_intent = {temporal_intent}")
        print(f"     conflict_keywords = {conflict_kws}")
        print(f"     时间戳加分窗口:")
        for pattern, start_d, end_d in _TIME_KEYWORD_WINDOWS:
            if pattern.search(query):
                print(f"       '{pattern.pattern}' -> [{start_d}, {end_d}] 前")
    else:
        print(f"     temporal_intent = None")

    # 3. 候选列表（boost 前 = 检索原始返回）
    print(f"  --- 3. 检索候选列表 (boost 前，{len(results_pre_boost)} 条) ---")
    _print_result_table(results_pre_boost)

    # 4. 候选列表（boost 后）
    print(f"  --- 4. 时间加分重排后 ({len(results_post_boost)} 条) ---")
    _print_result_table(results_post_boost)

    # 5. 最终传入 PromptAssembler 的记忆（按类型分区）
    print(f"  --- 5. 传入 PromptAssembler 的记忆 ---")
    if results_post_boost:
        for i, r in enumerate(results_post_boost):
            mt = getattr(r.item.metadata, "memory_type", "unknown")
            rf = getattr(r.item.metadata, "relative_time", "")
            ef = getattr(r.item.metadata, "event_type", "")
            tags = f"type={mt}" + (f" time={rf}" if rf else "") + (f" ev={ef}" if ef else "")
            print(f"     [{i + 1}] score={r.score:.4f} [{tags}] {r.item.content[:60]}")
    else:
        print(f"     (无)")

    # 6. LLM 上下文（按 section 展示）
    print(f"  --- 6. 交给 LLM 的记忆上下文 ---")
    if assembler_context:
        for msg in assembler_context:
            if msg["role"] == "system":
                content = msg["content"]
                # 提取各 section
                sections = [
                    "<retrieved_factual_memories>", "<retrieved_dialog_history>",
                    "<task_context>", "<recent_chitchat>", "<other_memory>"
                ]
                for sec in sections:
                    if sec in content:
                        idx_start = content.find(sec)
                        idx_end = len(content)
                        for s2 in sections:
                            pos = content.find(s2, idx_start + len(sec))
                            if pos != -1 and pos < idx_end:
                                idx_end = pos
                        snippet = content[idx_start:idx_end].strip()[:300]
                        print(f"     [{sec.strip('<>')}] {snippet}")
    else:
        print(f"     (无)")
    print(f"{sep}\n")


def _print_result_table(results: list):
    """打印候选记忆表格（含元数据标注）"""
    if not results:
        print(f"     (空)")
        return
    for i, r in enumerate(results):
        ts = r.item.timestamp
        ts_str = ts.strftime("%Y-%m-%d %H:%M:%S UTC") if ts else "N/A"
        src = str(r.source)
        content = r.item.content[:70].replace("\n", " ")
        meta = r.item.metadata

        # 元数据摘要
        mt = getattr(meta, "memory_type", "?")
        rt = getattr(meta, "relative_time", "")
        et = getattr(meta, "event_type", "")
        is_fact = getattr(meta, "is_factual_memory", True)
        kws = getattr(meta, "keywords", [])

        # 诊断标注
        tags = []
        if not is_fact or mt == "query":
            tags.append("NOISE(query)")
        if rt:
            tags.append(f"relative_time={rt}")
        if et:
            tags.append(f"event={et}")
        if kws:
            tags.append(f"kw={','.join(kws[:3])}")
        if mt and mt not in ("unknown", "event"):
            tags.append(f"type={mt}")

        tag_str = f"  <-- {', '.join(tags)}" if tags else ""
        print(f"     [{i + 1}] source={src}  score={r.score:.4f}{tag_str}")
        print(f"         timestamp = {ts_str}")
        print(f"         content   = {content}")
        if i < len(results) - 1:
            print(f"")


def reset_memory(agent_id: str, sqlite: SQLiteLogStorage, vector_store: VectorStore):
    """清除指定 agent 的所有记忆数据"""
    db_path = f"demo_{agent_id}.db"
    # 清除 SQLite
    if os.path.exists(db_path):
        os.remove(db_path)
    # 清除向量库
    try:
        vector_store.delete_by_agent(agent_id)
    except Exception:
        pass  # ChromaDB 可能没有该 agent 的数据
    print(f"[System] 已清除 {agent_id} 的全部记忆数据")


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

    do_reset = "--reset" in sys.argv
    do_debug = "--debug-retrieval" in sys.argv

    if do_debug:
        print("[System] 调试模式已开启 — 每次查询将打印完整检索追踪日志")

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

    if do_reset:
        reset_memory(agent_id, sqlite, vector_store)
        # 重新初始化存储，因为 SQLite 文件已被删除
        ingest, retrieve, cache, sqlite, vector_store = setup_memory(agent_id)

    llm = LLMClient()
    assembler = PromptAssembler(agent_name=agent_name)
    classifier = create_intent_classifier(use_llm=False)

    base_system_prompt = (
        f"你是{agent_name}，一位{agent_role}。"
        f"你在 Datawhale 办公室工作。请基于你的记忆与用户自然对话。"
        f"如果记忆中有用户之前告诉过你的信息，请自然地引用。"
    )

    print(f"\n{'=' * 55}")
    print(f"  {agent_name}（{agent_role}）已上线")
    print(f"  输入消息开始对话，/quit 退出，/reset 重置记忆")
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
        if user_input == "/reset":
            reset_memory(agent_id, sqlite, vector_store)
            ingest, retrieve, cache, sqlite, vector_store = setup_memory(agent_id)
            print("[System] 记忆已重置，可以开始全新对话\n")
            continue

        turn += 1
        print()

        # ─── 1. 意图分类 ───
        intent = classifier.classify(user_input)
        if do_debug:
            print(f"  ┌─ [Intent] {intent.intent_type} | {intent.reasoning}")
            if intent.rewritten_queries:
                print(f"  │  rewritten_queries: {intent.rewritten_queries}")
            print(f"  └─")

        # ─── 2. 根据意图分流检索 ───
        if intent.intent_type == "chitchat":
            # 闲聊：跳过检索，仅取短期缓存
            cache.get_recent(5)  # 保持缓存活跃
            results = RetrievalResponse(original_query=user_input, agent_id=agent_id)
            context_messages = assembler.assemble(user_input, results)
            pre_boost_snapshot = []

        else:
            # 构建检索请求
            req_kwargs = dict(query=user_input, limit=5, agent_id=agent_id)
            if intent.intent_type == "qa_query":
                req_kwargs["metadata_filters"] = {"is_factual_memory": True}
                if intent.rewritten_queries:
                    req_kwargs["rewritten_queries"] = intent.rewritten_queries
            request = RetrievalRequest(**req_kwargs)
            results = retrieve.retrieve(request, agent_id=agent_id)

            pre_boost_snapshot = [RetrievedMemory(item=r.item, score=r.score, source=r.source)
                                  for r in results.results]

            results.results = apply_time_boost(user_input, results.results)

            # 调试输出
            if do_debug:
                context_messages = assembler.assemble(user_input, results)
                print_debug_retrieval(
                    query=user_input,
                    results_pre_boost=pre_boost_snapshot,
                    results_post_boost=results.results,
                    assembler_context=context_messages,
                )
            else:
                print(f"  ┌─ [Debug] 检索到 {len(results.results)} 条记忆")
                for i, mem in enumerate(results.results):
                    source = str(mem.source)
                    content_preview = mem.item.content[:60].replace("\n", " ")
                    print(f"  │  {i+1}. [{source}] score={mem.score:.4f} | {content_preview}...")
                print(f"  └─")

        # ─── 3. 组装上下文 → 生成回复 ───
        if not do_debug:
            context_messages = assembler.assemble(user_input, results)

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

        # ─── 4. 写入记忆（带意图标签）───
        ingest.process_message(
            content=user_input,
            role=MemoryRole.USER,
            agent_id=agent_id,
            intent_type=intent.intent_type,
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
