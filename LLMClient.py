# @Time    :2026/4/14 13:14
# @Author  :jinxi
# @File    :LLMClient.py
# @Software:PyCharm

from openai import OpenAI, AsyncOpenAI
from config import settings


class LLMClient:
    """LLM abstraction layer — supports any OpenAI-compatible API."""

    def __init__(self):
        self.sync_client = OpenAI(
            api_key=settings.ollama_api_key,
            base_url=settings.ollama_base_url
        )
        self.async_client = AsyncOpenAI(
            api_key=settings.ollama_api_key,
            base_url=settings.ollama_base_url
        )
        self.model = settings.ollama_model_id

    async def generate(self, system_prompt: str, messages: list) -> str:
        """Plain text generation — no tool use."""
        full_messages = [{"role": "system", "content": system_prompt}] + messages

        try:
            response = await self.async_client.chat.completions.create(
                model=self.model,
                messages=full_messages,
                temperature=0.7
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"LLM call failed: {e}")
            return "(NPC is lost in thought...)"

    async def generate_with_tools(
        self,
        system_prompt: str,
        messages: list,
        tools: list,
    ) -> dict:
        """
        Tool-aware generation.

        Args:
            system_prompt: system-level instruction
            messages: conversation history (List[dict] with role/content)
            tools: List[dict] in OpenAI function-calling format

        Returns:
            {
                "text": str | None,          # plain text reply (if no tool call)
                "tool_calls": [               # tool calls the model requested
                    {"name": str, "arguments": dict},
                    ...
                ],
                "finish_reason": str,
            }
        """
        full_messages = [{"role": "system", "content": system_prompt}] + messages

        try:
            response = await self.async_client.chat.completions.create(
                model=self.model,
                messages=full_messages,
                temperature=0.1,
                tools=tools,
                tool_choice="auto",
            )
            choice = response.choices[0]
            finish = choice.finish_reason

            tool_calls = []
            text = None

            if finish == "tool_calls" and choice.message.tool_calls:
                for tc in choice.message.tool_calls:
                    import json
                    try:
                        args = json.loads(tc.function.arguments)
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                    tool_calls.append({
                        "id": tc.id,
                        "name": tc.function.name,
                        "arguments": args,
                    })

            if choice.message.content:
                text = choice.message.content

            return {
                "text": text,
                "tool_calls": tool_calls,
                "finish_reason": finish,
            }

        except Exception as e:
            print(f"LLM tool call failed: {e}")
            return {
                "text": "(Agent is thinking...)",
                "tool_calls": [],
                "finish_reason": "error",
            }
