# test_schema.py
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

from datetime import datetime
from pydantic import ValidationError
import pytest
from Memory.schema.memory_item import MemoryItem, MemoryRole, MemoryStage
from Memory.schema.retrieval import RetrievedMemory
from Memory.schema.routing import BackendTarget


def test_basic_instantiation():
    """基础实例化与自动字段 (ID, Timestamp, Stage)"""
    item = MemoryItem(
        content="玩家进入了咖啡馆，点了一杯美式。",
        role=MemoryRole.USER
    )
    assert isinstance(item.id, str), "ID 应该是一个字符串"
    assert len(item.id) > 10, "UUID 格式不正确"
    assert isinstance(item.timestamp, datetime), "时间戳应该是 datetime 对象"
    assert item.stage == MemoryStage.SENSORY.value, f"默认阶段应为 {MemoryStage.SENSORY.value}"


def test_metadata_defaults_and_mutability():
    """Metadata 默认结构与可变性"""
    item = MemoryItem(content="NPC 觉得今天天气不错。", role=MemoryRole.ASSISTANT)
    assert item.metadata.importance == 0, "默认重要度应该为 0"
    assert item.metadata.location == "unknown", "默认地点应该为 unknown"

    item.metadata.importance = 8
    assert item.metadata.importance == 8, "Metadata 应该允许被修改"


def test_invalid_role_rejected():
    """非法角色被 Pydantic 拦截"""
    with pytest.raises(ValidationError):
        MemoryItem(content="我是外星人", role="alien")


def test_string_auto_maps_to_enum():
    """字符串自动映射为枚举类型"""
    sys_item = MemoryItem(content="系统初始化完成。", role="system")
    assert sys_item.role == "system", "未能正确处理字符串形式的枚举输入"


def test_retrieved_memory_nesting():
    """RetrievedMemory 嵌套模型包装"""
    base_memory = MemoryItem(content="关键记忆", role=MemoryRole.USER)
    result = RetrievedMemory(
        item=base_memory,
        score=0.92,
        source=BackendTarget.VECTOR
    )
    assert result.item.content == "关键记忆", "嵌套对象内容丢失"
    assert result.score == 0.92, "分数记录错误"
