# @Time    :2026/5/6 14:04
# @Author  :进喜
# @File    :conflict_resolver.py
# @Software:PyCharm

'''
冲突调解员（检测逻辑矛盾并选择解决策略）
    1.直接进行覆盖，但是容易误杀
    2.引入LLM，对新旧记忆进行判断，然后返回一个一致性记忆
    3.多维上下文，给关系加上时间或条件约束
困难点在于什么时候触发
    路线 A（主动防御 - 写入时查重 - 写时计算）：在 AsyncDispatcher 后台写入或提取三元组时，先拿着提取出的关键词去 L2 向量库搜一下有没有相似话题，如果有，就丢给 ConflictResolver 看看冲不冲突，冲突了就调用 UpdateHub。
        数据库干净、聊天响应快、最大风险是数据丢失：LLM 仲裁出错了（把没冲突的算作冲突给覆盖了），原始上下文就永远丢失了。适合事实性知识、偏好知识
    路线 B（懒加载 - 检索时调和 - 读时计算）：写入时不管它，就让冲突的数据同时存在库里。但是在 RetrieveHub 捞出多条结果后、喂给大模型组装前，如果发现针对同一个实体有几条打分都很高但意思相反的记忆，当场用 ConflictResolver 融合成一条，再扔进 Assembler。
        数据库充满冗余、聊天响应慢、上下文撑爆。 如果关于同一个实体有 20 条不同阶段的记忆，检索时会全部捞出来，可能撑爆 Token 限制。适合复杂情感、案情推理
    我们可以放心的选择路线 A ，因为分层存储策略的存在，用户原话已经作为流水账被安全锁在SQLite中了
'''
import json
import os
from typing import Optional
from openai import OpenAI
from ..schema.memory_item import MemoryItem
# ✨ 核心改动：引入我们统一的判决书契约
from ..schema.conflict import ConflictRecord, ConflictType, ResolutionStrategy
from dotenv import load_dotenv

class ConflictResolver:
    load_dotenv()
    """
    记忆冲突仲裁引擎 (Conflict Resolver)
    利用大模型的逻辑推理能力，对比新旧两段记忆，决定如何化解矛盾。
    """

    def __init__(self):
        model_name = os.getenv("OLLAMA_MODEL_ID") or "qwen"
        base_url = os.getenv("OLLAMA_BASE_URL") or "http://localhost:11434/v1"
        api_key = os.getenv("OLLAMA_API_KEY") or "ollama"
        self.model_name = model_name
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url)

    def resolve(self, old_memory: MemoryItem, new_statement: str) -> ConflictRecord:
        """
        核心仲裁入口：判断 new_statement 是否与 old_memory 产生冲突，并按 ConflictRecord 格式返回判决。
        """

        system_prompt = """
        你是一个客观严谨的“记忆档案管理员”。
        用户刚才说了一句新话（新线索），你需要在给定的“旧记忆”基础上，判断两者是否存在逻辑冲突或状态更新。

        【判断准则与策略】
        1. 状态推翻 (update_newer -> overwrite)：新线索推翻了旧记忆（例：旧“在北京上班”，新“搬去上海了”）。你需要返回覆盖策略，并可以在 merged_content 写出最新状态。
        2. 细节补充 (nuance_addition -> merge_new)：新线索是旧记忆的细化（例：旧“喜欢狗”，新“怕大型犬”）。你需要返回融合策略，并在 merged_content 写出综合状态。
        3. 互斥矛盾 (direct_contradiction -> overwrite / merge_new)：根据情况选择覆盖或融合。
        4. 无冲突 (keep_both)：两者完全不相干，或者只是普通的叠加。设置 has_conflict 为 false。

        【强制输出格式】
        必须严格输出 JSON 格式，字段必须与以下结构严格匹配：
        {
            "has_conflict": true/false,
            "conflict_type": "direct_contradiction" | "update_newer" | "nuance_addition" | null,
            "target_memory_id": "填入下方提供的旧记忆ID" | null,
            "strategy": "overwrite" | "keep_both" | "merge_new" | null,
            "merged_content": "融合后的文本" | null,
            "reasoning": "你的思考和推理过程"
        }
        """

        prompt_content = f"""
        【旧记忆 ID】: {old_memory.id}
        【旧记忆内容】: {old_memory.content} (记录时间: {old_memory.timestamp.strftime('%Y-%m-%d %H:%M')})

        【新线索】: {new_statement}

        请进行仲裁分析并返回 JSON。
        """

        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt_content}
                ],
                temperature=0.1  # 极低温度，保证逻辑一致性
            )

            json_str = response.choices[0].message.content
            # ✨ 使用 Pydantic 的能力直接将大模型的 JSON 映射成强类型对象
            result = ConflictRecord.model_validate_json(json_str)

            # 如果大模型忘了填 ID，我们帮它兜底补上
            if result.has_conflict and not result.target_memory_id:
                result.target_memory_id = old_memory.id

            return result

        except Exception as e:
            print(f"⚠️ [ConflictResolver 警告] 仲裁失败，默认安全策略为不冲突: {e}")
            # ✨ 失败时返回安全的默认 ConflictRecord
            return ConflictRecord(
                has_conflict=False,
                strategy=ResolutionStrategy.KEEP_BOTH,
                reasoning=f"仲裁异常，触发防崩溃兜底: {str(e)}"
            )