# @Time    :2026/5/13 08:47
# @Author  :进喜
# @File    :metrics.py
# @Software:PyCharm


# @Time    :2026/5/13 09:00
# @Author  :进喜
# @File    :metrics.py

from typing import List, Set, Dict, Any
import numpy as np


class RetrievalMetrics:
    """
    检索质量评估工具箱 (微观指标)
    专门评估 RetrieveHub 召回的记忆碎片准不准。
    """

    @staticmethod
    def calculate_hit_rate(retrieved_ids: List[str], expected_ids: Set[str]) -> float:
        """
        记忆命中率 (Hit Rate / Recall):
        在召回的记忆列表中，包含了多少我们预期必须找到的黄金记忆？
        :return: 0.0 ~ 1.0 的比例分
        """
        if not expected_ids:
            return 1.0

        hits = sum(1 for rid in retrieved_ids if rid in expected_ids)
        return hits / len(expected_ids)

    @staticmethod
    def calculate_mrr(retrieved_ids: List[str], expected_ids: Set[str]) -> float:
        """
        平均倒数排名 (Mean Reciprocal Rank):
        系统有没有把最关键的记忆排在最前面？排得越靠前，分数越高。
        """
        for index, rid in enumerate(retrieved_ids):
            if rid in expected_ids:
                return 1.0 / (index + 1)
        return 0.0

    @staticmethod
    def calculate_redundancy(retrieved_contents: List[str], similarity_func) -> float:
        """
        记忆冗余度评估 (Redundancy):
        测试召回的记忆是否高度同质化。分数越高，说明废话越多。
        :param similarity_func: 一个计算两段文本相似度的函数 (比如调向量打分)
        """
        if len(retrieved_contents) < 2:
            return 0.0

        total_sim = 0
        pairs = 0
        for i in range(len(retrieved_contents)):
            for j in range(i + 1, len(retrieved_contents)):
                sim = similarity_func(retrieved_contents[i], retrieved_contents[j])
                total_sim += sim
                pairs += 1

        return total_sim / pairs if pairs > 0 else 0.0


class GenerationMetrics:
    """
    生成质量与幻觉评估仪 (宏观指标)
    基于 "LLM-as-a-Judge" (让大模型当裁判) 机制，评估最终生成的回答是否忠实于记忆。
    """

    def __init__(self, llm_client):
        """
        :param llm_client: 你的大模型调用客户端，需要有一个 generate(prompt) 方法
        """
        self.llm_client = llm_client

    def evaluate_hallucination(self, question: str, answer: str, retrieved_memories: List[str]) -> Dict[str, Any]:
        """
        幻觉/忠实度检测 (Faithfulness):
        判断 AI 的回答是否仅仅基于召回的记忆，有没有胡编乱造用户的经历。
        """
        context = "\n".join([f"- {m}" for m in retrieved_memories])

        prompt = f"""
        你是一个严苛的记忆评估法官。请评估AI的【回答】是否完全忠实于【用户记忆上下文】，是否存在幻觉（编造了上下文中没有的事实）。

        【用户问题】: {question}
        【用户记忆上下文】: 
        {context}

        【AI回答】: {answer}

        请严格按以下 JSON 格式输出评估结果：
        {{
            "is_faithful": true/false,
            "hallucinated_facts": ["如果不忠实，列出编造的事实，如果忠实为空列表"],
            "score": 0.0到1.0的评分 (1.0表示完全忠实)
        }}
        """

        try:
            # 调用大模型进行裁判，假设客户端返回了 JSON 字符串
            response_json_str = self.llm_client.generate(prompt)
            # 在实际工程中这里会加 json.loads(response_json_str) 和容错处理
            import json
            result = json.loads(response_json_str)
            return result
        except Exception as e:
            print(f"⚠️ 幻觉评估失败: {e}")
            return {"is_faithful": False, "score": 0.0, "error": str(e)}

    def evaluate_forgetting_leak(self, answer: str, archived_memories: List[str]) -> bool:
        """
        ✨ 记忆衰退/存档防泄漏测试:
        你的 UpdatePolicy 可能会把旧记忆归档（比如前女友、前公司）。
        这个测试专门检查 AI 有没有“嘴碎”把不该提的旧事实混进现在的回答里。
        :return: True 表示发生泄漏，False 表示安全
        """
        # 简单暴力的关键词泄漏检测，实际也可以用 LLM Judge
        for old_fact in archived_memories:
            if old_fact in answer:
                return True
        return False