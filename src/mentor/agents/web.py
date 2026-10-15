from langchain.agents import create_agent
from mentor.tools import web_search, fetch_url, get_current_time
from mentor.utils.create_model import create_model
from mentor.utils.trace_wrapper import trace_wrapper
from mentor.utils.get_prompt import get_prompt
from mentor.middlewares.link_prompt_middleware import create_prompt_link_middleware


async def agent_factory():
    prompt = await get_prompt("web_agent")
    model = create_model(**prompt.config.get("model_config", {}))
    agent = create_agent(
        model=model,
        tools=[web_search, fetch_url, get_current_time],
        system_prompt=prompt.compile(),
        middleware=[create_prompt_link_middleware(prompt)],
    )
    return agent


agent_factory_with_trace = trace_wrapper(agent_factory)
