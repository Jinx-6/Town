"""
基于规则的轻量级元数据提取器
无 LLM 依赖，纯确定性规则，用于在记忆存入时补充结构化元数据。
"""
from typing import Optional


# ── 时间词 → 标准化相对时间 ──────────────────────────────
_RELATIVE_TIME_MAP: dict = {
    "昨天": "昨天", "昨日": "昨天",
    "前天": "前天", "前日": "前天",
    "今天": "今天", "今日": "今天",
    "最近": "最近", "这几天": "最近",
}

# 时间词检测模式（按长度降序，避免短词误匹配长词）
_TIME_PATTERNS = sorted(_RELATIVE_TIME_MAP.keys(), key=len, reverse=True)

# ── 事件类型 → 触发词 ────────────────────────────────────
_EVENT_TYPE_RULES: list = [
    ("repair", ["修", "修理", "维修", "修复", "修好"]),
    ("buy", ["买", "购买", "买了"]),
    ("help", ["帮", "帮忙", "帮助"]),
    ("ask", ["问", "问一下", "请教"]),
]

# ── 问句检测 ────────────────────────────────────────────
_QUESTION_MARKERS = ["？", "?", "吗", "什么", "你还记得", "记得吗",
                     "怎么", "如何", "能不能", "可以吗"]


def extract_metadata(content: str, *, existing_meta: Optional[dict] = None) -> dict:
    """
    从文本内容中提取结构化元数据。

    返回 dict，可直接合并到 MemoryMetadata：
      - memory_type
      - temporal_text
      - relative_time
      - event_type
      - keywords
      - is_factual_memory

    如果 existing_meta 中已有值，不会覆盖（用户显式传入优先）。
    """
    result = {}

    # 1. 时间词提取
    temporal_text = _extract_temporal_text(content)
    result["temporal_text"] = temporal_text
    result["relative_time"] = _map_relative_time(temporal_text)

    # 2. 问句检测 → 判定记忆类型
    is_question = _detect_question(content)
    if is_question:
        result["memory_type"] = "query"
        result["is_factual_memory"] = False
        result["event_type"] = "ask"
    else:
        result["memory_type"] = "event"
        result["is_factual_memory"] = True
        result["event_type"] = _infer_event_type(content)

    # 3. 关键词提取
    result["keywords"] = _extract_keywords(content)

    # 4. 不覆盖已有元数据
    if existing_meta:
        for key in list(result.keys()):
            existing_val = existing_meta.get(key)
            if existing_val not in (None, "", [], False):
                result[key] = existing_val

    return result


def _extract_temporal_text(content: str) -> str:
    """提取第一条匹配的时间词"""
    for pattern in _TIME_PATTERNS:
        if pattern in content:
            return pattern
    return ""


def _map_relative_time(temporal_text: str) -> str:
    """将原始时间词映射为标准相对时间"""
    if not temporal_text:
        return ""
    return _RELATIVE_TIME_MAP.get(temporal_text, temporal_text)


def _detect_question(content: str) -> bool:
    """是否为问句/查询语句"""
    c = content.strip()
    for marker in _QUESTION_MARKERS:
        if marker in c:
            return True
    return False


def _infer_event_type(content: str) -> str:
    """根据关键词推断事件类型"""
    for etype, triggers in _EVENT_TYPE_RULES:
        for t in triggers:
            if t in content:
                return etype
    return "general"


def _extract_keywords(content: str) -> list:
    """简单关键词提取：名词性词汇"""
    # 常见物品/对象词
    noun_candidates = [
        "发电机", "饮水机", "电脑", "手机", "灯", "门", "窗",
        "空调", "冰箱", "洗衣机", "汽车", "自行车",
        "水", "电", "饭", "咖啡", "茶",
        "报告", "代码", "文件", "bug",
    ]
    found = []
    for noun in noun_candidates:
        if noun in content:
            found.append(noun)
    return found
