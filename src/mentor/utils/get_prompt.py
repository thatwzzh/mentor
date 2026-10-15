from langfuse import get_client
import asyncio


async def get_prompt(agent_name):
    langfuse = get_client()
    prompt = await asyncio.to_thread(langfuse.get_prompt, agent_name)
    return prompt
