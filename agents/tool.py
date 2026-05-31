"""
Tool 协议定义 + 3 个内置工具。

Tool: 工具元数据 + 执行函数
ToolResult: 结构化返回
"""
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from datetime import datetime, timezone


# ── 结构化返回 ────────────────────────────────────────
@dataclass
class ToolResult:
    name: str = ""
    success: bool = False
    data: Any = None
    error: str = ""


# ── Tool 协议 ─────────────────────────────────────────
@dataclass
class Tool:
    name: str
    description: str
    parameters: Dict[str, Any]         # JSON Schema
    execute: Callable[..., ToolResult] # 同步，返回结构化结果
    enabled: bool = True
    return_direct: bool = False        # True → 结果直接返回用户，不经过 LLM 再加工

    def to_openai_schema(self) -> dict:
        """转为 OpenAI function calling 格式。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            }
        }


# ── 内置工具 ──────────────────────────────────────────

def _tool_search_own_memory(retrieve_hub, agent_id: str):
    """封装 RetrieveHub.retrieve() 为工具。"""

    def execute(query: str, limit: int = 5, memory_type: str = "") -> ToolResult:
        try:
            from Memory.schema.retrieval import RetrievalRequest

            filters = {}
            if memory_type:
                filters["memory_type"] = memory_type
            else:
                filters["is_factual_memory"] = True

            req = RetrievalRequest(
                query=query, limit=limit, agent_id=agent_id,
                metadata_filters=filters,
            )
            response = retrieve_hub.retrieve(req, agent_id=agent_id)

            items = []
            for r in response.results:
                meta = r.item.metadata
                items.append({
                    "content": r.item.content,
                    "score": round(r.score, 4),
                    "memory_type": getattr(meta, "memory_type", "?"),
                    "timestamp": r.item.timestamp.isoformat(),
                    "source": str(r.source),
                })
            return ToolResult(name="search_own_memory", success=True,
                               data={"results": items, "count": len(items)})
        except Exception as e:
            return ToolResult(name="search_own_memory", success=False, error=str(e))

    return Tool(
        name="search_own_memory",
        description="检索 Agent 自身的长期记忆。用于查找过去的事件、对话和用户提到的事实。",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索查询，用自然语言描述要找的内容",
                },
                "limit": {
                    "type": "integer",
                    "description": "返回结果数上限，默认 5",
                    "default": 5,
                },
                "memory_type": {
                    "type": "string",
                    "description": "记忆类型过滤: event/query/dialogue/task/fact。留空默认只返回事实记忆。",
                    "enum": ["event", "query", "dialogue", "task", "fact", ""],
                },
            },
            "required": ["query"],
        },
        execute=execute,
    )


def _tool_get_current_time():
    """返回当前 UTC 时间。"""

    def execute() -> ToolResult:
        now = datetime.now(timezone.utc)
        return ToolResult(name="get_current_time", success=True, data={
            "utc": now.isoformat(),
            "timestamp": int(now.timestamp()),
        })

    return Tool(
        name="get_current_time",
        description="获取当前 UTC 时间及 Unix 时间戳。",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        execute=execute,
    )


def _tool_get_agent_state(agent_id: str, agent_name: str, agent_role: str,
                          get_state_fn):
    """返回 Agent 当前状态元信息。"""

    def execute() -> ToolResult:
        from agents.memory_aware_agent import AgentState
        state = get_state_fn() if get_state_fn else AgentState.IDLE
        return ToolResult(name="get_agent_state", success=True, data={
            "agent_id": agent_id,
            "agent_name": agent_name,
            "agent_role": agent_role,
            "state": state.value if hasattr(state, 'value') else str(state),
        })

    return Tool(
        name="get_agent_state",
        description="获取当前 Agent 的身份信息与运行状态。",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        execute=execute,
    )


# ── 构建内置工具集 ────────────────────────────────────
def create_builtin_tools(
    retrieve_hub,
    agent_id: str,
    agent_name: str = "",
    agent_role: str = "",
    get_state_fn: Callable = None,
) -> List[Tool]:
    """创建 3 个内置工具的实例。"""
    return [
        _tool_search_own_memory(retrieve_hub, agent_id),
        _tool_get_current_time(),
        _tool_get_agent_state(agent_id, agent_name, agent_role, get_state_fn),
    ]
