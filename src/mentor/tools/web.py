import asyncio

from langchain.tools import tool
from tavily import TavilyClient

from mentor.config.tavily_settings import tavily_settings


@tool
async def web_search(query: str):
    """根据关键字进行网络搜索，返回结构化的搜索结果"""
    client = TavilyClient(api_key=tavily_settings.api_key)
    response = await asyncio.to_thread(client.search, query=query)
    return response


@tool
async def fetch_url(urls: list[str]):
    """根据提供的url数组，抓取网页内容，返回结构化的搜索结果"""
    client = TavilyClient(api_key=tavily_settings.api_key)
    response = await asyncio.to_thread(client.extract, urls=urls, include_images=False)
    return response
