# @Time    :2026/5/10 10:54
# @Author  :进喜
# @File    :conflict.py
# @Software:PyCharm


from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field


# ==========================================
# 1. 冲突类型枚举 (定义了会发生什么矛盾)
# ==========================================
class ConflictType(str, Enum):
    """定义新旧记忆碰撞时的化学反应"""
    DIRECT_CONTRADICTION = "direct_contradiction"  # 纯互斥矛盾 (例：旧=喜欢红，新=讨厌红)
    UPDATE_NEWER = "update_newer"  # 状态随时间更新 (例：旧=在A公司上班，新=跳槽到了B公司)
    NUANCE_ADDITION = "nuance_addition"  # 细节补充/条件限制 (例：旧=喜欢喝咖啡，新=只喝冰美式)


# ==========================================
# 2. 解决策略枚举 (定义了系统该怎么做)
# ==========================================
class ResolutionStrategy(str, Enum):
    """定义系统最终对底层数据库执行的动作"""
    OVERWRITE = "overwrite"  # 直接用新记忆覆盖旧记忆（废弃旧的）
    KEEP_BOTH = "keep_both"  # 互不干扰，两者皆保留（可能只是在不同情境下发生的）
    MERGE_NEW = "merge_new"  # 融合两者，生成一条更全面、更准确的新记忆


# ==========================================
# 3. 核心契约：冲突判决书 (Conflict Record)
# ==========================================
class ConflictRecord(BaseModel):
    """
    冲突判定与解决结果包
    这是 Processor 调用完大模型后，必须严格返回的结构化数据。
    """
    has_conflict: bool = Field(..., description="是否检测到与已有历史记忆的冲突")

    # --- 如果有冲突，以下字段必须填充 ---
    conflict_type: Optional[ConflictType] = Field(
        None, description="检测到的冲突具体类型"
    )
    target_memory_id: Optional[str] = Field(
        None, description="被撞库撞出的那条【老记忆】的唯一ID"
    )
    strategy: Optional[ResolutionStrategy] = Field(
        None, description="大模型建议的解决动作"
    )
    merged_content: Optional[str] = Field(
        None, description="如果策略是MERGE_NEW，这里存放AI重新融合后的一句完整且准确的新记忆文本"
    )
    reasoning: str = Field(
        default="", description="大模型做出这个判决的内在逻辑（推理链），对Debug极其重要！"
    )