# @Time    :2026/5/12 13:57
# @Author  :进喜
# @File    :update_policy.py
# @Software:PyCharm


from enum import Enum
from pydantic import BaseModel, Field
from typing import Dict, Optional


class UpdateAction(str, Enum):
    """面对事实冲突时的四大裁决动作"""
    OVERWRITE = "overwrite"  # 喜新厌旧：直接用新事实覆盖旧事实 (适用于：现居地、现任职位)
    APPEND = "append"  # 兼容并包：保留旧的，同时增加新的 (适用于：兴趣爱好、去过的城市)
    ARCHIVE = "archive"  # 历史存档：将旧关系打上“过去式”标签，存入新关系 (适用于：曾就职于)
    IGNORE = "ignore"  # 拒不承认：新信息的置信度太低，当做没听见


class UpdateConfig(BaseModel):
    """
    更新策略配置表 (Schema)
    维护着不同类型的“关系谓词(Predicate)”该如何演化的规则。
    """
    # 默认策略：如果遇到不认识的关系，保守起见，一律追加，不删旧数据
    default_action: UpdateAction = Field(default=UpdateAction.APPEND)

    # 关系路由表：定义现实世界中各种属性的演化规律
    # (在生产环境中，这通常会做成一个可以动态下发的 JSON 或数据库配置)
    predicate_rules: Dict[str, UpdateAction] = Field(default_factory=lambda: {
        "居住在": UpdateAction.OVERWRITE,
        "当前城市": UpdateAction.OVERWRITE,
        "现任职位": UpdateAction.OVERWRITE,

        "喜欢": UpdateAction.APPEND,
        "讨厌": UpdateAction.APPEND,
        "去过": UpdateAction.APPEND,

        "就职于": UpdateAction.ARCHIVE,
        "就读于": UpdateAction.ARCHIVE
    })

    # 置信度门槛：如果大模型从最近对话中抽取的新事实低于 0.7 分，连触发更新的资格都没有
    min_confidence_threshold: float = Field(default=0.7)


class UpdatePolicy:
    """
    图谱更新裁判官 (Knowledge Graph Update Policy)
    当抽取引擎发现图谱中已经存在同一主体的同类关系时，调用此策略进行裁决。
    """

    def __init__(self, config: Optional[UpdateConfig] = None):
        self.config = config or UpdateConfig()

    def resolve_conflict(self, existing_edge: dict, new_edge: dict, new_confidence: float) -> UpdateAction:
        """
        核心裁决逻辑：当新旧边发生碰撞时，给出行动指南。

        :param existing_edge: 数据库里的旧数据，例如 {"start": "用户", "rel": "居住在", "end": "北京"}
        :param new_edge: 刚提取的新数据，例如 {"start": "用户", "rel": "居住在", "end": "上海"}
        :param new_confidence: 新数据提取的置信度得分 (0.0 ~ 1.0)
        :return: UpdateAction 行动指令
        """
        # 1. 垃圾信息防御：新信息太不可靠，直接忽略
        if new_confidence < self.config.min_confidence_threshold:
            return UpdateAction.IGNORE

        # 2. 提取关系谓词 (Predicate)
        rel = new_edge.get("rel", "").strip()

        # 3. 查表裁决：根据关系的性质决定如何演化
        action = self.config.predicate_rules.get(rel, self.config.default_action)

        # 4. 兜底逻辑：如果新旧指向的目的地完全一样（比如还是居住在北京），那当做无事发生
        if existing_edge.get("end") == new_edge.get("end"):
            return UpdateAction.IGNORE

        return action