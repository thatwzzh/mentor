"""函数级内存缓存工具。"""

import asyncio
import inspect
import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from functools import wraps
from typing import Any


_logger = logging.getLogger(__name__)
_refresh_executor = ThreadPoolExecutor(thread_name_prefix="func-cache-refresh")


def _freeze(value: Any) -> Any:
    """把常见容器参数转换成可哈希的缓存键，并保留类型信息。"""
    if isinstance(value, dict):
        return (dict, frozenset((_freeze(key), _freeze(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return (type(value), tuple(_freeze(item) for item in value))
    if isinstance(value, (set, frozenset)):
        return (type(value), frozenset(_freeze(item) for item in value))

    try:
        hash(value)
    except TypeError as exc:
        raise TypeError(f"参数类型 {type(value).__name__} 无法用于缓存键") from exc
    return (type(value), value)


def func_cache(*, ttl: int = 120, lazy: bool = False):
    """按函数和参数缓存结果，支持同步函数与异步函数。

    同一参数在首次调用或到期刷新时只执行一次，其余调用等待同一结果。
    开启 ``lazy`` 后，到期且已有缓存时立即返回旧值，并在后台刷新。
    刷新失败时保留旧值；首次计算失败时将异常传给所有等待者。

    参数：
        ttl: 缓存有效时间，单位为秒，必须大于等于零。
        lazy: 到期时是否先返回旧值，再在后台刷新。
    """
    if ttl < 0:
        raise ValueError("ttl 不能小于零")

    def decorator(func):
        signature = inspect.signature(func)
        cache: dict[Any, tuple[Any, float]] = {}
        inflight: dict[Any, Future[Any]] = {}
        lock = threading.Lock()

        def make_key(*args, **kwargs):
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            return tuple((name, _freeze(value)) for name, value in bound.arguments.items())

        def publish(key, future: Future[Any], *, result: Any = None, error: BaseException | None = None):
            with lock:
                if error is None:
                    cache[key] = (result, time.monotonic() + ttl)
                inflight.pop(key, None)

            if error is None:
                future.set_result(result)
            else:
                future.set_exception(error)

        def get_state(key):
            """在锁内决定返回缓存、等待计算，还是发起一次计算。"""
            with lock:
                entry = cache.get(key)
                if entry is not None and time.monotonic() < entry[1]:
                    return "cached", entry[0], None

                future = inflight.get(key)
                if entry is not None and lazy:
                    if future is None:
                        future = Future()
                        inflight[key] = future
                        return "refresh", entry[0], future
                    return "cached", entry[0], None

                if future is not None:
                    return "wait", None, future

                future = Future()
                inflight[key] = future
                return "compute", None, future

        if inspect.iscoroutinefunction(func):

            async def calculate_async(key, future: Future[Any], args, kwargs, *, background: bool):
                try:
                    result = await func(*args, **kwargs)
                except BaseException as exc:
                    publish(key, future, error=exc)
                    if background:
                        _logger.exception("异步缓存后台刷新失败：%s", func.__qualname__)
                else:
                    publish(key, future, result=result)

            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                key = make_key(*args, **kwargs)
                state, value, future = get_state(key)
                if state == "cached":
                    return value
                if state in ("compute", "refresh"):
                    assert future is not None
                    try:
                        asyncio.get_running_loop().create_task(
                            calculate_async(key, future, args, kwargs, background=state == "refresh")
                        )
                    except BaseException as exc:
                        publish(key, future, error=exc)
                        if state == "refresh":
                            _logger.exception("异步缓存后台刷新启动失败：%s", func.__qualname__)
                if state == "refresh":
                    return value
                assert future is not None
                # 等待者取消时不取消共享计算，其他等待者仍可取得结果。
                return await asyncio.shield(asyncio.wrap_future(future))

            return async_wrapper

        def calculate_sync(key, future: Future[Any], args, kwargs, *, background: bool):
            try:
                result = func(*args, **kwargs)
            except BaseException as exc:
                publish(key, future, error=exc)
                if background:
                    _logger.exception("同步缓存后台刷新失败：%s", func.__qualname__)
            else:
                publish(key, future, result=result)

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            key = make_key(*args, **kwargs)
            state, value, future = get_state(key)
            if state == "cached":
                return value
            if state == "refresh":
                assert future is not None
                try:
                    _refresh_executor.submit(calculate_sync, key, future, args, kwargs, background=True)
                except BaseException as exc:
                    publish(key, future, error=exc)
                    _logger.exception("同步缓存后台刷新启动失败：%s", func.__qualname__)
                return value
            assert future is not None
            if state == "compute":
                calculate_sync(key, future, args, kwargs, background=False)
            return future.result()

        return sync_wrapper

    return decorator
