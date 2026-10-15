from langchain.agents.middleware import wrap_model_call
from langfuse import propagate_attributes


def create_prompt_link_middleware(prompt):
    @wrap_model_call
    async def link_prompt(request, handler):
        with propagate_attributes(prompt=prompt):
            return await handler(request)

    return link_prompt
