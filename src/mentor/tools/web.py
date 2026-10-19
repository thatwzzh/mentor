"""联网工具：基于 Tavily 的网页搜索与 URL 正文抓取。"""

import asyncio

from langchain.tools import tool
from tavily import TavilyClient

from mentor.config.tavily_settings import tavily_settings


@tool
async def web_search(query: str):
    """根据关键字进行网络搜索，返回结构化的搜索结果"""
    client = TavilyClient(api_key=tavily_settings.api_key)
    # Tavily SDK 是同步客户端，放到线程池执行，避免阻塞事件循环。
    response = await asyncio.to_thread(client.search, query=query)
    return response


@tool
async def fetch_url(urls: list[str]):
    """根据提供的url数组，抓取网页内容，返回结构化的搜索结果"""
    client = TavilyClient(api_key=tavily_settings.api_key)
    # 同样使用线程池；当前未设置超时，长网页可能长时间占用工具槽位。
    response = await asyncio.to_thread(client.extract, urls=urls, include_images=False)
    return response
