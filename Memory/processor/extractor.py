# @Time    :2026/4/22 15:07
# @Author  :进喜
# @File    :extractor.py
# @Software:PyCharm

# 知识图谱抽取
import json
import os
from pathlib import Path
from dotenv import load_dotenv
from pydantic import ValidationError
from typing import Optional
from openai import OpenAI
# ✨ 核心改动：从 Schema 中引入数据契约
from ..schema.graph import ExtractionResult


class GraphExtractor:
    """
    智能知识抽取层 (Information Extraction)
    作为纯粹的执行引擎，负责阅读原始对话文本，提取实体及关系，为 L3 级图谱记忆提供弹药。
    此任务极其耗时，强制要求在后台事件队列中执行！
    """

    def __init__(self):
        current_file = Path(__file__).resolve()
        town_dir = current_file.parent.parent.parent
        env_path = town_dir / '.env'
        load_dotenv(dotenv_path=env_path)

        model_name = os.getenv("OLLAMA_MODEL_ID") or "qwen"
        base_url = os.getenv("OLLAMA_BASE_URL") or "http://localhost:11434/v1"
        api_key = os.getenv("OLLAMA_API_KEY") or "ollama"

        self.model_name = model_name

        # 🌟 关键：在这里直接传参，不要依赖环境变量的自动读取
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url
        )

    def extract(self, text: str, user_name: str = "用户", agent_name: str = "张三") -> ExtractionResult:
        """核心入口：调用 LLM 从文本中提取符合 ExtractionResult 结构的三元组"""

        system_prompt = f"""
        你是一个严谨的知识图谱数据清洗专家。你的任务是从对话文本中提取出有价值的实体关系，并将其转化为结构化的三元组 (主语, 谓语, 宾语)。

        【抽取法则】
        1. 核心代词替换：如果文本中出现“我”，请极其严格地替换为“{user_name}”；如果出现“你”，请替换为“{agent_name}”。这对于图谱的绝对实体唯一性至关重要！
        2. 价值过滤：忽略无意义的寒暄（如“你好”、“今天天气不错”），只提取具有持久记忆价值的事实、偏好、人物关系或事件。
        3. 实体归一化：主语和宾语应当是名词性的实体（人名、机构名、物品名、概念名），不要使用长句作为实体。
        4. 强制输出：你必须严格返回 JSON 格式，包含一个 'triplets' 列表。如果无关系可抽，请返回 {{ "triplets": [] }}。

        【示例】
        文本："我明天要去城投公司开会，老王也会去。"
        输出 JSON 示例：
        {{
            "triplets": [
                {{"subject": "{user_name}", "predicate": "将要去", "object": "城投公司"}},
                {{"subject": "老王", "predicate": "将要去", "object": "城投公司"}}
            ]
        }}
        """

        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"请提取以下文本中的知识三元组：\n{text}"}
                ],
                temperature=0.1  # 保持低温度，避免模型凭空捏造事实
            )

            json_str = response.choices[0].message.content
            # ✨ 直接反序列化为 Schema 对象
            result = ExtractionResult.model_validate_json(json_str)

            if result.triplets:
                print(f"🧩 [Extractor] 成功从文本中提取了 {len(result.triplets)} 个关系。")

            return result

        except (ValidationError, json.JSONDecodeError) as e:
            print(f"⚠️ [Extractor 警告] LLM 抽取格式错误或校验失败，触发静默降级。详情: {e}")
            return ExtractionResult(triplets=[])
        except Exception as e:
            print(f"❌ [Extractor 致命错误] API 调用失败: {e}")
            return ExtractionResult(triplets=[])