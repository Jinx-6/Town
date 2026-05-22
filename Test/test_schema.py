# test_schema.py
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))  # 这行是关
from datetime import datetime
from pydantic import ValidationError
from Memory.schema.memory_item import MemoryItem, MemoryRole, MemoryStage, RetrievalResult


def run_schema_tests():
    print("🚀 启动 Schema 模块防弹测试...")
    passed = 0
    total = 5

    # ==========================================
    # 测试 1：基础功能与自动生成字段
    # ==========================================
    try:
        item = MemoryItem(
            content="玩家进入了咖啡馆，点了一杯美式。",
            role=MemoryRole.USER
        )
        assert isinstance(item.id, str), "ID 应该是一个字符串"
        assert len(item.id) > 10, "UUID 格式不正确"
        assert isinstance(item.timestamp, datetime), "时间戳应该是 datetime 对象"
        # 因为你设置了 use_enum_values = True，所以这里的值应该是字符串 "sensory" 而不是 Enum 对象
        assert item.stage == MemoryStage.SENSORY.value, f"默认阶段应为 {MemoryStage.SENSORY.value}"

        print("✅ [Test 1] 基础实例化与自动字段 (ID, Timestamp, Stage) 测试通过！")
        passed += 1
    except Exception as e:
        print(f"❌ [Test 1] 失败: {e}")

    # ==========================================
    # 测试 2：Metadata 默认值注入
    # ==========================================
    try:
        item2 = MemoryItem(content="NPC 觉得今天天气不错。", role=MemoryRole.ASSISTANT)
        assert "importance" in item2.metadata, "metadata 缺少 importance 字段"
        assert item2.metadata["importance"] == 0, "默认重要度应该为 0"
        assert item2.metadata["location"] == "unknown", "默认地点应该为 unknown"

        # 测试修改 metadata
        item2.metadata["importance"] = 8
        assert item2.metadata["importance"] == 8, "Metadata 应该允许被修改"

        print("✅ [Test 2] Metadata 默认结构与可变性测试通过！")
        passed += 1
    except Exception as e:
        print(f"❌ [Test 2] 失败: {e}")

    # ==========================================
    # 测试 3：Pydantic 类型拦截（防御性测试）
    # ==========================================
    try:
        # 我们故意传入一个不存在的角色 "alien"，看看能不能被拦截
        bad_item = MemoryItem(content="我是外星人", role="alien")
        print("❌ [Test 3] 失败: 竟然允许非法角色创建，Pydantic 拦截失效！")
    except ValidationError as e:
        # 捕获到了 ValidationError，说明拦截成功！
        print("✅ [Test 3] 非法角色拦截测试通过（成功拦截了错误的 Role）！")
        passed += 1

    # ==========================================
    # 测试 4：字符串自动转 Enum (宽容度测试)
    # ==========================================
    try:
        # 我们传入普通的字符串 "system"，而不是 MemoryRole.SYSTEM
        # Pydantic 应该能自动识别并转换
        sys_item = MemoryItem(content="系统初始化完成。", role="system")
        assert sys_item.role == "system", "未能正确处理字符串形式的枚举输入"
        print("✅ [Test 4] 字符串自动映射枚举类型测试通过！")
        passed += 1
    except Exception as e:
        print(f"❌ [Test 4] 失败: {e}")

    # ==========================================
    # 测试 5：RetrievalResult 嵌套模型测试
    # ==========================================
    try:
        base_memory = MemoryItem(content="关键记忆", role=MemoryRole.USER)
        result = RetrievalResult(
            item=base_memory,
            score=0.92,
            source_db="vector_db"
        )
        assert result.item.content == "关键记忆", "嵌套对象内容丢失"
        assert result.score == 0.92, "分数记录错误"
        print("✅ [Test 5] RetrievalResult 包装类组装测试通过！")
        passed += 1
    except Exception as e:
        print(f"❌ [Test 5] 失败: {e}")

    # ==========================================
    # 结果汇总
    # ==========================================
    print("-" * 40)
    if passed == total:
        print(f"🎉 完美！所有 {total} 个 Schema 测试全部通过，你的防弹衣质量满分！")
    else:
        print(f"⚠️ 注意：通过了 {passed}/{total} 个测试，请检查失败项。")


if __name__ == "__main__":
    run_schema_tests()