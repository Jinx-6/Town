# @Time    :2026/5/10 11:26
# @Author  :进喜
# @File    :graph.py
# @Software:PyCharm


'''

'''
from pydantic import BaseModel, Field
from typing import List

# ==========================================
# 1. 契约定义：严格的三元组输出结构
# ==========================================
class KnowledgeTriplet(BaseModel):
    """单一实体关系三元组"""
    subject: str = Field(..., description="主语实体，例如 '张三', '城投公司', '用户'")
    predicate: str = Field(..., description="关系谓词，尽量精简，例如 '任职于', '喜欢', '投资了', '父亲是'")
    object: str = Field(..., description="宾语实体，例如 '地方税务局', '咖啡', '李四'")

class ExtractionResult(BaseModel):
    """抽取结果合集"""
    triplets: List[KnowledgeTriplet] = Field(
        default_factory=list,
        description="从文本中提取出的所有三元组列表"
    )