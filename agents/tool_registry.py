"""
ToolRegistry — 工具注册与调度中心。

注册 → 按名查找 → 按 enabled 过滤 → 执行（dispatch）。
"""
from typing import Dict, List, Optional
from agents.tool import Tool, ToolResult


class ToolRegistry:
    """工具注册表：注册、查找、调度。"""

    def __init__(self):
        self._tools: Dict[str, Tool] = {}

    # ── 注册 ───────────────────────────────────────
    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def register_all(self, tools: List[Tool]) -> None:
        for t in tools:
            self.register(t)

    # ── 查找 ───────────────────────────────────────
    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def list_all(self) -> List[Tool]:
        return list(self._tools.values())

    def list_enabled(self) -> List[Tool]:
        return [t for t in self._tools.values() if t.enabled]

    @property
    def names(self) -> List[str]:
        return list(self._tools.keys())

    # ── 调度 ───────────────────────────────────────
    def dispatch(self, name: str, **kwargs) -> ToolResult:
        """
        按名执行工具。

        Args:
            name: 工具名
            **kwargs: 传给 execute() 的参数

        Returns:
            ToolResult: success=True 时 data 含执行结果
        """
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(name=name, success=False,
                              error=f"工具 '{name}' 未注册。可用: {self.names}")

        if not tool.enabled:
            return ToolResult(name=name, success=False,
                              error=f"工具 '{name}' 已禁用。")

        try:
            # 工具可能不需要参数
            if not kwargs:
                return tool.execute()
            return tool.execute(**kwargs)
        except TypeError as e:
            return ToolResult(name=name, success=False,
                              error=f"参数错误: {e}")
        except Exception as e:
            return ToolResult(name=name, success=False,
                              error=f"执行异常: {e}")

    def to_openai_schemas(self) -> List[dict]:
        """输出所有 enabled 工具的 OpenAI function calling 格式。"""
        return [t.to_openai_schema() for t in self.list_enabled()]

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
