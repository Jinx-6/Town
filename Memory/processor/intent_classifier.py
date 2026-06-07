"""
意图分类与查询重写引擎。

- RuleBasedIntentClassifier：确定性规则，无外部依赖，当前默认实现
- LLMIntentClassifier：LLM 增强版，复用 QueryPlanner 的 API 模式，后续按需启用

两者共享 IntentClassification 输出契约。
"""
import json
from typing import List, Literal, Optional
from pydantic import BaseModel, Field
from LLMClient import LLMClient


# ── 输出契约 ──────────────────────────────────────────
class IntentClassification(BaseModel):
    intent_type: Literal["fact_statement", "task_instruction", "qa_query", "chitchat"]
    reasoning: str = ""
    rewritten_queries: List[str] = Field(default_factory=list)
    extracted_entities: List[str] = Field(default_factory=list)
    extracted_event_type: str = "general"
    is_factual: bool = True


# ── 规则版分类器 ──────────────────────────────────────
class RuleBasedIntentClassifier:
    """基于规则的意图分类器（当前默认）。"""

    QUESTION_MARKERS = [
        "？", "?", "吗", "什么", "哪", "谁", "怎么", "如何",
        "你还记得", "记得吗", "能不能", "可以吗", "做了", "做过",
        "之前", "上次", "以前", "最近", "这几天", "说过"
    ]

    CHITCHAT_PATTERNS = [
        "哈哈", "嗯", "好的", "谢谢", "多谢", "ok", "OK", "好滴",
        "行", "知道了", "明白", "哦", "好吧", "没事", "没关系",
        "你好", "嗨", "哈喽", "拜拜", "再见", "晚安", "早安"
    ]

    TASK_MARKERS = [
        "帮我", "查一下", "查询", "提醒我", "搜索",
        "写一封", "写个", "帮我写", "帮我做", "生成", "创建",
        "帮我查", "帮我找", "帮我搜"
    ]

    # 查询重写的同义词映射
    _SYNONYM_MAP = {
        "修": ["修", "修理", "维修", "修复", "修好"],
        "买": ["买", "购买", "买了"],
        "帮": ["帮", "帮忙", "帮助"],
        "做": ["做", "做了", "完成", "完成"],
        "什么": ["什么", "哪些", "哪个"],
    }

    def classify(self, user_input: str) -> IntentClassification:
        text = user_input.strip()

        # 1. 问答检测 — 问号或过去询问词
        if self._is_qa_query(text):
            return IntentClassification(
                intent_type="qa_query",
                reasoning="检测到问句或过去信息询问",
                rewritten_queries=self._rewrite(text),
                is_factual=False
            )

        # 2. 任务指令检测
        if self._is_task(text):
            return IntentClassification(
                intent_type="task_instruction",
                reasoning="检测到任务/指令关键词",
                is_factual=False
            )

        # 3. 闲聊检测 — 短 + 无信息量
        if self._is_chitchat(text):
            return IntentClassification(
                intent_type="chitchat",
                reasoning="检测为闲聊/反馈",
                is_factual=False
            )

        # 4. 默认事实陈述
        entities = self._extract_entities(text)
        event_type = self._infer_event_type(text)
        return IntentClassification(
            intent_type="fact_statement",
            reasoning="默认归类为事实陈述",
            extracted_entities=entities,
            extracted_event_type=event_type,
            is_factual=True
        )

    # ── 四类检测 ──────────────────────────────────
    def _is_qa_query(self, text: str) -> bool:
        for m in self.QUESTION_MARKERS:
            if m in text:
                return True
        return False

    def _is_task(self, text: str) -> bool:
        for m in self.TASK_MARKERS:
            if text.startswith(m) or m in text:
                return True
        return False

    def _is_chitchat(self, text: str) -> bool:
        t = text.strip().lower()
        for p in self.CHITCHAT_PATTERNS:
            if t == p or t.startswith(p):
                return True
        # 超短输入且无信息量（3 字以内且不含动词/名词关键词）
        if len(t) <= 3 and not self._is_qa_query(text) and not self._is_task(text):
            return True
        return False

    # ── 查询重写 ──────────────────────────────────
    def _rewrite(self, text: str) -> List[str]:
        """去问号/语气词，生成 2-3 条搜索变体。"""
        # 去掉问号和常见语气词
        cleaned = text.replace("？", " ").replace("?", " ").replace("，", " ")
        for noise in ["你还记得", "记得吗", "能不能", "可以吗", "吗", "呢", "啊"]:
            cleaned = cleaned.replace(noise, " ")

        variants = [cleaned.strip()]

        # 同义词扩展：对每个变体中的关键词做替换
        for key, syns in self._SYNONYM_MAP.items():
            if key in cleaned:
                for syn in syns:
                    if syn != key:
                        expanded = cleaned.replace(key, syn)
                        variants.append(expanded.strip())
                        break  # 只加一条同义变体

        # 关键词组合变体：拆成短词
        parts = [p.strip() for p in cleaned.split() if len(p.strip()) >= 1]
        if len(parts) >= 2:
            variants.append(" ".join(parts[1:] + [parts[0]]))  # 词序调换

        # 去重，保留 2-4 条
        seen = set()
        unique = []
        for v in variants:
            if v and v not in seen:
                seen.add(v)
                unique.append(v)
        return unique[:4]

    # ── 实体与事件提取 ────────────────────────────
    def _extract_entities(self, text: str) -> List[str]:
        """从事实陈述中提取实体名词。"""
        noun_candidates = [
            "发电机", "饮水机", "电脑", "手机", "灯", "门", "窗",
            "空调", "冰箱", "洗衣机", "汽车", "自行车",
            "水", "电", "饭", "咖啡", "茶",
            "报告", "代码", "文件", "bug", "Bug",
        ]
        found = []
        for noun in noun_candidates:
            if noun in text:
                found.append(noun)
        return found

    def _infer_event_type(self, text: str) -> str:
        event_triggers = [
            ("repair", ["修", "修理", "维修", "修复", "修好"]),
            ("buy", ["买", "购买", "买了"]),
            ("help", ["帮", "帮忙", "帮助"]),
            ("ask", ["问", "问一下", "请教"]),
        ]
        for etype, triggers in event_triggers:
            for t in triggers:
                if t in text:
                    return etype
        return "general"


# ── LLM 增强版（占位，后续启用）───────────────────────
class LLMIntentClassifier:
    """
    LLM 驱动的意图分类器（增强版）。
    调用模式复用 QueryPlanner 的 OpenAI 兼容 API。
    当本地模型不可用时降级为 RuleBasedIntentClassifier。
    """

    def __init__(self, llm: LLMClient = None):
        self._llm = llm or LLMClient()
        self.model_name = self._llm.model

    @property
    def client(self):
        return self._llm.sync_client

    def classify(self, user_input: str) -> IntentClassification:
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                temperature=0.1,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": _LLM_SYSTEM_PROMPT},
                    {"role": "user", "content": f"请分析以下用户输入：\n{user_input}"},
                ]
            )
            raw = response.choices[0].message.content
            data = json.loads(raw)
            return IntentClassification(**data)
        except Exception:
            return RuleBasedIntentClassifier().classify(user_input)


_LLM_SYSTEM_PROMPT = """你是一个企业级 AI Agent 的意图分类与查询重写引擎。分析用户输入，完成三件事：
1. 分类为恰好一种意图类型
2. 如果输入是事实陈述，提取实体
3. 如果输入是问答查询，重写/扩展为多个搜索查询

【意图类型】
- "fact_statement": 陈述已发生或正在发生的事实（"我昨天修了饮水机"、"我住在上海"）
- "task_instruction": 可执行的指令或请求（"帮我查天气"、"提醒我开会"）
- "qa_query": 询问过去的信息（"我昨天做了什么？"、"还记得我的密码吗？"）
- "chitchat": 闲聊、反馈、寒暄（"哈哈"、"好的谢谢"、"嗯嗯"）

【分类法则】
1. 含问号或询问过去的词 → qa_query
2. 请求执行动作 → task_instruction
3. 短反馈/寒暄 → chitchat
4. 以上都不符合 → fact_statement

【输出格式】
{
    "intent_type": "fact_statement",
    "reasoning": "一句话说明",
    "rewritten_queries": [],
    "extracted_entities": [],
    "extracted_event_type": "general",
    "is_factual": true
}"""


# ── 统一入口 ──────────────────────────────────────────
def create_intent_classifier(use_llm: bool = False):
    if use_llm:
        return LLMIntentClassifier()
    return RuleBasedIntentClassifier()
