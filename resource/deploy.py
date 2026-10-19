"""部署工具接口示例；不参与运行时，正式实现见 src/mentor/tools/deploy.py。"""

from typing import TypedDict

from langchain.tools import tool
from pydantic import BaseModel, Field


class DeployInput(BaseModel):
    path: str = Field(
        description="""
        沙箱内的产物路径，它可以是文件夹，也可以是文件。
        这些路径表示要最终交付给用户的产物。
        比如以下都是有效的 path 值：
        /root/workspace/my-vue-app/dist
        /root/workspace/my-vue-app/assets/charts.png
        /root/workspace/my-vue-app/other.tar.gz
        如果指定的是目录，该工具会自动递归读取该目录下的所有文件进行部署
        """
    )
    target_dirname: str = Field(
        description="""
        部署的目标目录名称。
        该工具会把 path 参数指定的产物放到云存储的某个存储路径中。
        存储路径是： <前缀>/<target_dirname>/。
        前缀是什么你无需关心，你只需要指定 target_dirname 即可。
        建议该值直接取用 workspace 中的工程名字。
        """
    )
    entry_files: list[str] = Field(
        min_length=1,
        description="""
        指定部署完成过后，要访问的入口文件名称。
        比如：["index.html"、"static/chart.png"]
        该工具会自动拼接好完整的 URL 访问地址，最终交付给用户访问
        """,
    )


class DeployOutput(TypedDict):
    # 部署的总文件数量
    total_count: int
    # 成功部署的文件数量
    success_count: int
    # 失败的文件数量
    failure_count: int
    # 失败的原因，键为沙箱内文件的绝对路径，值是错误描述
    failures: dict[str, str]
    # 用户访问产物的入口地址集合
    urls: list[str]


@tool(args_schema=DeployInput)
async def deploy(
    path: str, target_dirname: str, entry_files: list[str]
) -> DeployOutput:
    """
    将沙箱内的文件或目录部署到云存储，返回部署统计、失败原因和入口访问地址。

    参数：
        path: 沙箱内产物的路径；目录会递归部署其中的所有文件。
        target_dirname: 云存储中的目标目录名称，存储路径为 <前缀>/<target_dirname>/。
        entry_files: 要交付给用户的入口文件路径，相对于部署后的目标目录。

    返回：
        DeployOutput 字典，包含以下字段：
        total_count: 本次部署的总文件数量。
        success_count: 成功部署的文件数量。
        failure_count: 部署失败的文件数量；为 0 表示所有文件部署成功。
        failures: 失败文件及原因，键为沙箱内文件的绝对路径，值为错误描述。
        urls: 根据 entry_files 拼接的入口访问地址列表。

    调用示例（目录部署，全部成功）：

    deploy(
        "/root/workspace/vue-app/dist",
        "vue-app",
        ["index.html", "static/chart.png"]
    )

    得到结果示例：
    {
        "total_count": 5,
        "success_count": 5,
        "failure_count": 0,
        "failures": {},
        "urls": [
            "http://deploy.com/auto-prefix/vue-app/index.html",
            "http://deploy.com/auto-prefix/vue-app/static/chart.png"
        ]
    }

    调用示例（单文件部署，全部成功）：

    deploy(
        "/root/workspace/deep-research/dist/reporter.zip",
        "deep-research",
        ["reporter.zip"]
    )

    得到结果示例：

    {
        "total_count": 1,
        "success_count": 1,
        "failure_count": 0,
        "failures": {},
        "urls": [
            "http://deploy.com/auto-prefix/deep-research/reporter.zip"
        ]
    }
    """
    ...


deploy.handle_tool_error = True
