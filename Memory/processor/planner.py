# @Time    :2026/4/22 14:35
# @Author  :进喜
# @File    :planner.py
# @Software:PyCharm


import json
import os
from pydantic import BaseModel, Field, ValidationError
from typing import List, Optional
from openai import OpenAI
from dotenv import load_dotenv


# ==========================================
# 1. 契约定义：严格的 JSON 输出结构
# ==========================================
class RetrievalInstruction(BaseModel):
    """单一检索指令（子任务）"""
    intent: str = Field(..., description="拆解出的子意图说明，例如'查询昨天的行程'")
    search_query: str = Field(...,
                              description="用于 L2 向量库搜索的自然语言，包含同义词扩展。例如'昨天 去了哪里 出行 地点'")
    keywords: List[str] = Field(default_factory=list,
                                description="用于 L0/L1 数据库的精准匹配关键词，例如['昨天', '去']")
    time_filter: Optional[str] = Field("all", description="时间范围约束：'today', 'yesterday', 'all'")


class RetrievalPlan(BaseModel):
    """完整的检索计划合集"""
    original_query: str = Field(..., description="用户的原始提问")
    instructions: List[RetrievalInstruction] = Field(..., description="拆解后的多个独立检索指令")


# ==========================================
# 2. Planner 引擎核心 (LLM 驱动)
# ==========================================
class QueryPlanner:
    """
    智能意图拆解与规划层
    依赖大模型的推理能力，进行 Query 重写、多意图拆解和语义联想。
    """
    load_dotenv()
    def __init__(self):
        # 实例化 LLM 客户端
        # 通过传入 base_url，你可以无缝切换到本地部署模型或其他厂商的 API
        model_name = os.getenv("OLLAMA_MODEL_ID") or "qwen"
        base_url = os.getenv("OLLAMA_BASE_URL") or "http://localhost:11434/v1"
        api_key = os.getenv("OLLAMA_API_KEY") or "ollama"
        self.model_name = model_name
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url
        )

    def generate_plan(self, raw_query: str) -> RetrievalPlan:
        """核心入口：调用真实的 LLM 生成结构化检索计划"""

        system_prompt = """
        你是一个企业级 AI Agent 的核心记忆分析师。你的任务是深度解析用户的原始提问，并将其拆解为多个精准的底层数据库检索指令。

        【执行法则】
        1. 意图剥离：如果问题包含多个独立意图（例如同时询问不同时间、不同维度的事件），必须将其拆分为多个独立的指令对象。
        2. 向量扩展 (search_query)：为 L2 向量库生成检索词时，必须进行语义联想，加入同义词和上下文可能涉及的词汇（如：提问"税务"，需联想"城投、债务、发票"等）。
        3. 极简提取 (keywords)：为 L0 缓存库提取必须精准匹配的核心词汇，通常不超过 3 个词。
        4. 强制契约：你必须严格按照要求的 JSON 结构输出，不可包含任何额外的 Markdown 标记或解释说明。

        【输出格式要求】
        {
            "original_query": "用户原始输入",
            "instructions": [
                {
                    "intent": "子意图描述",
                    "search_query": "联想扩展后的搜索词",
                    "keywords": ["精准词1", "精准词2"],
                    "time_filter": "all"
                }
            ]
        }
        """

        try:
            # 发起 LLM 请求
            response = self.client.chat.completions.create(
                model=self.model_name,
                response_format={"type": "json_object"},  # 强制大模型以 JSON 模式输出
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"请拆解以下用户提问：{raw_query}"}
                ],
                temperature=0.1  # 极低的温度，抑制大模型的发散，确保每次拆解的稳定性
            )

            # 解析并验证 JSON 契约
            json_str = response.choices[0].message.content
            plan = RetrievalPlan.model_validate_json(json_str)
            return plan

        except (ValidationError, json.JSONDecodeError) as e:
            # 🛡️ 企业级降级保护 (Fallback)：
            # 当大模型“抽风”未返回合规 JSON，或者网络波动断开时，系统绝对不能崩溃。
            # 我们直接生成一个基于原问题的保守检索计划，保障基础对话能够继续。
            print(f"⚠️ [Planner 警告] LLM 结构化输出解析失败，触发安全降级机制。详情: {e}")
            return RetrievalPlan(
                original_query=raw_query,
                instructions=[
                    RetrievalInstruction(
                        intent="Fallback 降级全局检索",
                        search_query=raw_query,
                        keywords=[raw_query[:2]],
                        time_filter="all"
                    )
                ]
            )
        except Exception as e:
            print(f"❌ [Planner 致命错误] LLM API 调用失败: {e}")
            raise e