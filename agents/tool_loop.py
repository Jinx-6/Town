"""
Tool loop — LLM 决策调用工具、执行、收集结果的可控循环。

不替代 respond()，不修改 ingest 路径。
ToolResult → ToolLoopResult.tool_results → AgentResponse.tool_results
              → PromptAssembler.assemble(tool_results=...)
              → <tool_result> section（不进入 factual memory）
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any

from agents.tool import ToolResult


@dataclass
class ToolLoopResult:
    messages: List[Dict[str, Any]] = field(default_factory=list)
    tool_results: List[Dict[str, Any]] = field(default_factory=list)
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    reached_max_rounds: bool = False
    errors: List[str] = field(default_factory=list)


async def execute_tool_loop(
    *,
    llm_client,
    registry,
    initial_messages: List[Dict[str, str]],
    system_prompt: str,
    max_rounds: int = 3,
) -> ToolLoopResult:
    """
    有限工具调用循环。

    1. 将 messages 发给 LLM (generate_with_tools)
    2. LLM 决定调哪些工具 → dispatch → 收集 ToolResult
    3. 工具结果追加到 messages（OpenAI tool role）
    4. 循环直到 LLM 不再要求工具 or 达到 max_rounds

    Args:
        llm_client: 提供 generate_with_tools()
        registry: ToolRegistry
        initial_messages: 初始对话消息 [{"role": "user", "content": "..."}]
        system_prompt: 系统提示
        max_rounds: 最大工具调用轮数

    Returns:
        ToolLoopResult:
          - messages: 累积的对话消息（含 tool call/result）
          - tool_results: 所有 ToolResult 的 dict 列表
          - tool_calls: 所有 tool call 的 {name, arguments} 列表
          - reached_max_rounds: True → 达到上限被截断
          - errors: 错误信息列表
    """
    tools_schema = registry.to_openai_schemas()
    messages = list(initial_messages)
    errors: List[str] = []
    all_tool_results: List[Dict[str, Any]] = []
    all_tool_calls: List[Dict[str, Any]] = []

    for round_idx in range(max_rounds):
        resp = await llm_client.generate_with_tools(
            system_prompt=system_prompt,
            messages=messages,
            tools=tools_schema,
        )

        # LLM 决定不调工具 → 退出
        if not resp.get("tool_calls"):
            break

        # 记录本轮 assistant 的 tool_calls
        tool_call_msgs = []
        for tc in resp["tool_calls"]:
            name = tc["name"]
            args = tc.get("arguments", {})

            all_tool_calls.append({"name": name, "arguments": args})

            # 调度工具
            result: ToolResult = registry.dispatch(name, **args)
            all_tool_results.append({
                "name": result.name,
                "success": result.success,
                "data": result.data,
                "error": result.error,
            })

            if not result.success:
                errors.append(f"[{name}] {result.error}")

            tool_call_msgs.append({
                "role": "assistant",
                "tool_calls": [{
                    "id": tc.get("id", f"call_{round_idx}_{name}"),
                    "type": "function",
                    "function": {"name": name, "arguments": args},
                }],
            })
            tool_call_msgs.append({
                "role": "tool",
                "tool_call_id": tc.get("id", f"call_{round_idx}_{name}"),
                "content": _format_tool_result_for_llm(result),
            })

        messages.extend(tool_call_msgs)

    return ToolLoopResult(
        messages=messages,
        tool_results=all_tool_results,
        tool_calls=all_tool_calls,
        reached_max_rounds=len(all_tool_calls) > 0 and len(errors) == 0,
        errors=errors,
    )


def _format_tool_result_for_llm(result: ToolResult) -> str:
    """将 ToolResult 格式化为 LLM 可读的简短文段。"""
    if not result.success:
        return f"error: {result.error}"
    return str(result.data)
