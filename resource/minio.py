"""MinIO 异步接口契约；仅声明类型和行为，不包含实现。"""

from dataclasses import dataclass, field
from datetime import timedelta
from typing import BinaryIO

# 默认缓存时间为 90 天，单位为秒。
_DEFAULT_MAX_AGE = 7776000


@dataclass
class CacheStrategy:
    """按文件后缀配置上传对象的 Cache-Control。

    extensions: 后缀列表，例如 [".png", ".jpg"]；匹配时忽略大小写。
        空列表不匹配任何文件。后缀必须以点开头且非空。
    max_age: 缓存秒数，必须大于等于 0；0 表示 no-store，
        大于 0 表示 max-age=<秒数>。默认值为 90 天。
    """

    extensions: list[str]
    max_age: int = _DEFAULT_MAX_AGE


@dataclass
class ContentTypeStrategy:
    """配置上传对象的默认 Content-Type 和文件后缀映射。

    default_content_type: 未匹配后缀或没有后缀时使用的非空 Content-Type，
        默认为 application/octet-stream。
    extensions: 后缀到非空 Content-Type 的映射，例如
        {".html": "text/html; charset=utf-8", ".png": "image/png"}。
        后缀必须以点开头且非空；匹配时忽略大小写，
        忽略大小写后重复的后缀视为不合法配置。

    write_file、upload_file 和 upload_dir 均按目标对象键最后一段的最后一个后缀匹配，
    例如 archive.tar.gz 匹配 .gz；映射优先于默认值。
    不根据本地文件名、文件内容或系统 MIME 配置自动推断类型。
    """

    default_content_type: str = "application/octet-stream"
    extensions: dict[str, str] = field(default_factory=dict)


@dataclass
class UploadDirResult:
    """目录上传完成后的汇总结果。

    total_count: 本次需要上传的普通文件总数，不包含跳过的文件或目录。
    success_count: 上传成功的文件数量。
    failure_count: 上传失败的文件数量。
    failures: 每个失败文件的原因；键为相对于 source_dir 的本地路径，
        分隔符统一为 /，不包含目标 prefix；值为包含异常类型和错误消息的字符串。

    total_count = success_count + failure_count；failure_count = len(failures)。
    无可上传文件时三个数量均为 0，failures 为 {}。
    """

    total_count: int
    success_count: int
    failure_count: int
    failures: dict[str, str]


class MinIO:
    """计划使用 aiobotocore 实现的 MinIO 异步操作接口。

    通用约定：
        - bucket_name 遵循服务端桶命名规则。
        - filename 是非空对象键，例如 "a/b/c/1.html"，不包含桶名。
          开头的 / 会被移除；不解析 .. 或合并中间的 /。
          去除开头的 / 后仍为空时，抛出 ValueError。
        - 除 create_bucket 外，方法不自动创建桶或修改访问权限。
        - 参数不合法抛出 ValueError；本地文件错误保留对应 OSError。
          服务端和网络错误原样向调用方传播，不转换为成功或空结果；
          upload_dir 的单文件错误例外，记录到汇总结果中。
        - 所有方法目前仅为契约占位，不可作为可运行客户端使用。
    """

    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        secure: bool = True,
        cache_strategy: list[CacheStrategy] | None = None,
        content_type_strategy: ContentTypeStrategy | None = None,
    ) -> None:
        """声明客户端配置；构造阶段不连接服务端。

        Args:
            endpoint: API 地址，例如 localhost:9002；不含协议、路径或查询参数。
            access_key: 非空访问密钥。
            secret_key: 非空秘密密钥。
            secure: True 使用 HTTPS，False 使用 HTTP。
            cache_strategy: None 或空列表表示所有上传文件均 no-store。
                未匹配的文件也使用 no-store；多个策略匹配时后者覆盖前者。
                策略仅影响此客户端后续上传的对象，不修改已存在对象。
            content_type_strategy: 上传时使用的 Content-Type 策略。
                None 等同于默认策略，即所有文件均使用 application/octet-stream。
                策略仅影响后续上传的对象，不修改已存在对象。

        Raises:
            ValueError: 地址、密钥、缓存策略或 Content-Type 策略不合法。
        """
        ...

    async def create_bucket(
        self, bucket_name: str, allow_public_read: bool = True
    ) -> None:
        """确保桶存在，并应用公共读约定。

        Args:
            bucket_name: 桶名；已存在时跳过建桶，仍应用公共读约定。
            allow_public_read: True 时允许任何人匿名读取桶内所有对象，
                不授予匿名写入、删除或列举权限，并保留无关的策略语句。
                False 时不新增公共读权限，也不撤销已有策略；不保证桶私有。

        建桶和设置策略不是原子操作；策略设置失败时桶可能已经创建，
        错误向调用方传播。重复调用不重复添加同一公共读策略。
        """
        ...

    async def bucket_exists(self, bucket_name: str) -> bool:
        """桶不存在时返回 False，存在时返回 True；权限和网络错误仍抛出。"""
        ...

    async def remove_bucket(self, bucket_name: str, force: bool = False) -> None:
        """删除桶；桶不存在时忽略。

        Args:
            bucket_name: 要删除的桶名。
            force: False 时仅删除空桶，非空时抛出服务端错误。
                True 时先删除桶内全部对象、历史版本和删除标记，再删除桶，
                无论桶是否为空。已删除的数据不可恢复。

        强制删除不是原子操作，失败时可能已经删除部分数据，不回滚。
        权限不足、对象保留限制或并发写入等导致删除失败时，向调用方抛出错误；
        force 不绕过服务端权限或保留规则，也不将失败视为成功。
        """
        ...

    async def write_file(
        self,
        *,
        bucket_name: str,
        filename: str,
        stream: BinaryIO,
    ) -> None:
        """从二进制流当前位置读取到结尾，上传为指定对象。

        同名对象覆盖当前内容；开启版本控制时由服务端生成新版本。
        应用匹配的缓存策略。空流允许上传为零字节对象。
        Content-Type 由构造器中的 content_type_strategy 按 filename 决定。
        调用方负责关闭流；不回到流起点、不关闭流，失败后不保证流位置。
        文本应先编码为字节。
        """
        ...

    async def upload_file(
        self,
        *,
        bucket_name: str,
        filename: str,
        source_file: str,
    ) -> None:
        """上传单个本地文件；覆盖和缓存约定与 write_file 相同。

        source_file 为本地路径，必须指向可读的普通文件。
        Content-Type 由构造器中的 content_type_strategy 按目标 filename 决定。
        本方法负责打开并关闭本地文件，不修改本地内容。
        """
        ...

    async def upload_dir(
        self,
        *,
        bucket_name: str,
        source_dir: str,
        prefix: str = "",
        max_concurrency: int = 10,
    ) -> UploadDirResult:
        """递归上传本地目录中的普通文件，保留相对目录结构。

        Args:
            bucket_name: 已存在的目标桶名。
            source_dir: 本地目录路径；不存在时抛出 FileNotFoundError，
                不是目录时抛出 NotADirectoryError。
            prefix: 目标对象键前缀，默认为空；移除首尾的 / 后，
                非空时通过一个 / 与文件相对路径连接。
            max_concurrency: 同时进行的文件上传数量上限，默认为 10，
                必须为正整数，否则抛出 ValueError。

        例如 source_dir 下的 images/logo.png，在 prefix="assets" 时
        上传为 assets/images/logo.png；不包含 source_dir 自身的目录名。
        相对路径的分隔符统一为 /。包含隐藏文件，跳过空目录和符号链接；
        其他非普通文件也跳过，无可上传文件时返回空汇总结果。
        每个文件的覆盖、缓存和 Content-Type 约定与 upload_file 相同，
        均按最终对象键匹配策略。不删除桶内未包含在本地目录中的对象。
        不修改本地内容；负责关闭本次上传打开的文件。
        上传前完成目录扫描，确定本次需要上传的文件集合。
        参数校验或目录扫描失败时直接抛出异常，不开始上传。
        并发上传时，单个文件的读取、服务端或网络错误均记录到 failures，
        不影响其他文件继续上传；等待全部文件处理完成后返回汇总结果。
        例如 images/logo.png 上传失败时，failures 的键为 "images/logo.png"，
        即使 prefix="assets"，也不包含 "assets/"。
        调用方取消任务时仍传播取消，不作为单文件失败处理。
        已完成的上传不回滚，整个目录上传不是原子操作。

        Returns:
            UploadDirResult：总数量、成功数量、失败数量及逐文件失败原因。
        """
        ...

    async def read_file(self, *, bucket_name: str, filename: str) -> bytes:
        """读取对象的全部内容为 bytes；空对象返回 b""。

        对象不存在时抛出服务端错误。返回前释放响应连接；
        全量读取会占用与对象大小相应的内存。
        """
        ...

    async def download_file(
        self,
        *,
        bucket_name: str,
        filename: str,
        target_file: str,
        overwrite: bool = False,
    ) -> None:
        """下载对象到本地 target_file，自动创建缺失的父目录。

        目标已存在且 overwrite=False 时抛出 FileExistsError；
        overwrite=True 时仅在下载完整成功后替换目标文件。
        失败时保留原目标文件，清理本次下载的临时文件并抛出异常。
        目标为目录时抛出 IsADirectoryError。
        """
        ...

    async def file_exists(self, *, bucket_name: str, filename: str) -> bool:
        """对象不存在时返回 False；桶不存在、权限或网络错误仍抛出。"""
        ...

    async def list_files(self, bucket_name: str, prefix: str = "") -> list[str]:
        """返回匹配前缀的全部对象键，按对象键升序排列。

        prefix 允许为空，开头的 / 会被移除，采用字符串前缀匹配。
        包含所有分页及嵌套前缀，返回键不加开头的 /，无匹配返回 []。
        仅列举当前可见对象，不列举历史版本或删除标记。
        桶不存在时抛出服务端错误。
        """
        ...

    async def remove_file(self, *, bucket_name: str, filename: str) -> None:
        """删除指定对象；对象不存在时忽略，桶不存在时抛出服务端错误。

        开启版本控制时遵循服务端默认删除语义，不清除历史版本。
        """
        ...

    def get_file_url(self, *, bucket_name: str, filename: str) -> str:
        """返回未签名的永久对象 URL，对对象键进行 URL 编码并保留 /。

        根据 secure 选择协议，不发起网络请求、不检查对象是否存在。
        URL 不授予权限；匿名访问需要桶策略允许公共读。
        """
        ...

    async def presigned_get_file_url(
        self,
        *,
        bucket_name: str,
        filename: str,
        expires: timedelta = timedelta(hours=1),
    ) -> str:
        """返回临时 GET 预签名 URL，不检查对象是否存在。

        expires 必须是 1 秒至 7 天之间的整数秒，否则抛出 ValueError。
        持有 URL 的人可在签名及签名所用凭据有效期间读取对象，
        实际有效期受凭据有效期和服务端权限约束；不修改桶策略。
        """
        ...

    async def presigned_put_file_url(
        self,
        *,
        bucket_name: str,
        filename: str,
        expires: timedelta = timedelta(hours=1),
    ) -> str:
        """返回临时 PUT 预签名 URL，用于直接上传指定对象。

        expires 必须是 1 秒至 7 天之间的整数秒，否则抛出 ValueError。
        不检查桶或对象是否存在，不创建桶、不上传内容、不修改桶策略。
        持有 URL 的人可在签名及签名所用凭据有效期间上传该对象，
        实际有效期受凭据有效期和服务端写入权限约束。
        调用方使用 HTTP PUT，将文件的二进制内容作为请求体，
        不使用 multipart/form-data；同名对象覆盖和版本约定与 write_file 相同。
        URL 在有效期内可重复使用，不是一次性上传凭证。
        不签入 Content-Type 或 Cache-Control，也不自动应用构造器中的策略；
        需要这些元数据时，由调用方在 PUT 请求中设置对应请求头。
        """
        ...
