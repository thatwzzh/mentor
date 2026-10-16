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
import uuid
import asyncio

_MEMEORY_STORE = InMemoryStore()

_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
_NAMESPACE = ("__sandbox__",)


async def _get_store(store: BaseStore, key: str, field_name: str) -> str | None:
    dict = await store.aget(_NAMESPACE, key)
    if dict is None:
        return None
    return dict.value.get(field_name, None)


async def _create(
    *,
    sandbox_config: OpenSandboxConfig,
    connection_config: ConnectionConfig,
    key: str,
    store: BaseStore,
) -> Sandbox:
    v_name = await _get_store(store, key, "volume")
    if v_name is None:
        v_name = str(uuid.uuid4()).replace("-", "")
    v = Volume(
        name=v_name,
        pvc=PVC(
            claimName=v_name, createIfNotExists=True, deleteOnSandboxTermination=False
        ),
        mountPath="/root",
    )
    sandbox = await Sandbox.create(
        image=sandbox_config.image,
        # 连接沙箱Server的配置
        connection_config=connection_config,
        # 沙箱存活时间
        timeout=timedelta(seconds=sandbox_config.timeout_seconds),
        # 等待沙箱准备就绪（容器拉起）的时间
        ready_timeout=timedelta(seconds=sandbox_config.ready_timeout_seconds),
        # 挂载卷柜
        volumes=[v],
    )

    await store.aput(_NAMESPACE, key, {"sandbox_id": sandbox.id, "volume": v_name})
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


async def get_sandbox(key: str | None = None, store: BaseStore | None = None):
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
            key=key,
            store=store,
        )
        return sandbox


@asynccontextmanager
async def enter_sandbox(key: str | None = None, store: BaseStore | None = None):
    sandbox = await get_sandbox(key, store)
    config = await get_config("sandbox_config", OpenSandboxConfig)
    await sandbox.renew(timedelta(seconds=config.timeout_seconds))
    yield sandbox
    await sandbox.renew(timedelta(seconds=config.timeout_seconds))
    await sandbox.close()  # 关闭沙箱连接
