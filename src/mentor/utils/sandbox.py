from contextlib import asynccontextmanager
from langgraph.store.base import BaseStore
from langgraph.runtime import get_runtime
from langgraph.store.memory import InMemoryStore
from weakref import WeakValueDictionary
from opensandbox import Sandbox
from opensandbox.models import PVC, Volume
from mentor.config.nacos.nacos_schemas import OpenSandboxConfig
from mentor.config.nacos.nacos_config import get_config
from datetime import timedelta
from opensandbox.config import ConnectionConfig
from opensandbox.exceptions import SandboxApiException
from mentor.config.sandbox_settings import sandbox_settings
from pydantic import BaseModel, Field
import uuid
import asyncio

_MEMEORY_STORE = InMemoryStore()

_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
_NAMESPACE = ("__sandbox__",)

SHARED_VOLUME_NAME = "sandbox-shared-data"
SHARED_SANDBOX_KEY = "sandbox-shared"


class VolumeConfig(BaseModel):
    mount_path: str
    name: str
    readonly: bool = Field(default=False)


async def _get_store(store: BaseStore, key: str, field_name: str) -> str | None:
    dict = await store.aget(_NAMESPACE, key)
    if dict is None:
        return None
    return dict.value.get(field_name, None)


async def _create(
    *,
    key: str,
    store: BaseStore,
    sandbox_config: OpenSandboxConfig,
    connection_config: ConnectionConfig,
    volume_configs: list[VolumeConfig] | None = None,
) -> Sandbox:
    if volume_configs is None:
        volume_configs = []
    volumes = [
        Volume(
            name=v.name,
            mountPath=v.mount_path,
            readOnly=v.readonly,
            pvc=PVC(
                claimName=v.name,
                createIfNotExists=True,
                deleteOnSandboxTermination=False,
            ),
        )
        for v in volume_configs
    ]
    sandbox = await Sandbox.create(
        image=sandbox_config.image,
        # 连接沙箱Server的配置
        connection_config=connection_config,
        # 沙箱存活时间
        timeout=timedelta(seconds=sandbox_config.timeout_seconds),
        # 等待沙箱准备就绪（容器拉起）的时间
        ready_timeout=timedelta(seconds=sandbox_config.ready_timeout_seconds),
        # 挂载卷柜
        volumes=volumes,
    )
    await store.aput(_NAMESPACE, key, {"sandbox_id": sandbox.id})
    return sandbox


async def _connect(
    *,
    connection_config: ConnectionConfig,
    key: str,
    store: BaseStore,
) -> Sandbox | None:
    "连接已有沙箱"
    sandbox_id = await _get_store(store, key, "sandbox_id")
    if sandbox_id is None:
        return None
    try:
        sandbox = await Sandbox.connect(
            sandbox_id=sandbox_id, connection_config=connection_config
        )
        return sandbox
    except SandboxApiException as exc:
        if exc.status_code == 404:
            return None
        else:
            raise


async def get_sandbox(
    key: str | None = None,
    store: BaseStore | None = None,
    volume_configs: list[VolumeConfig] | None = None,
):
    # 参数归一化
    if key is None:
        key = str(uuid.uuid4())
    if store is None:
        try:
            runtime = get_runtime()
            store = runtime.store
            if store is None:
                store = _MEMEORY_STORE
        except:
            store = _MEMEORY_STORE
    sandbox_config = await get_config("sandbox_config", OpenSandboxConfig)
    connection_config = ConnectionConfig(
        domain=sandbox_settings.domain,
        api_key=sandbox_settings.api_key,
        use_server_proxy=True,
    )
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        sandbox = await _connect(
            connection_config=connection_config,
            key=key,
            store=store,
        )
        if sandbox:
            return sandbox
        sandbox = await _create(
            sandbox_config=sandbox_config,
            connection_config=connection_config,
            store=store,
            key=key,
            volume_configs=volume_configs,
        )
        return sandbox


async def get_shared_sandbox(store: BaseStore | None = None):
    return await get_sandbox(
        SHARED_SANDBOX_KEY,
        store,
        [VolumeConfig(mount_path="/shared", name=SHARED_VOLUME_NAME, readonly=False)],
    )


@asynccontextmanager
async def enter_sandbox(
    key: str | None = None,
    store: BaseStore | None = None,
    volume_configs: list[VolumeConfig] | None = None,
):
    sandbox = await get_sandbox(key, store, volume_configs)
    config = await get_config("sandbox_config", OpenSandboxConfig)
    await sandbox.renew(timedelta(seconds=config.timeout_seconds))
    yield sandbox
    await sandbox.renew(timedelta(seconds=config.timeout_seconds))
    await sandbox.close()  # 关闭沙箱连接
