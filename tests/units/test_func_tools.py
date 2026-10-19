"""func_cache 单元测试：覆盖命中、参数键、并发单飞、TTL 与 lazy 刷新。"""

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from mentor.utils.func_tools import func_cache


def test_sync_hit_and_parameter_keys():
    calls = []

    @func_cache()
    def calculate(value):
        calls.append(value)
        return value * 2

    assert calculate(2) == 4
    assert calculate(2) == 4
    assert calculate(3) == 6
    assert calls == [2, 3]


def test_sync_keyword_arguments_are_part_of_cache_key():
    calls = 0

    @func_cache()
    def combine(*, left, right):
        nonlocal calls
        calls += 1
        return left + right

    assert combine(left=1, right=2) == 3
    assert combine(left=1, right=2) == 3
    assert combine(left=1, right=3) == 4
    assert calls == 2


def test_sync_cache_isolated_by_function():
    calls = []

    @func_cache()
    def first(value):
        calls.append("first")
        return value + 1

    @func_cache()
    def second(value):
        calls.append("second")
        return value + 2

    assert first(1) == 2
    assert second(1) == 3
    assert first(1) == 2
    assert second(1) == 3
    assert calls == ["first", "second"]


def test_sync_none_result_is_cached():
    calls = 0

    @func_cache()
    def empty():
        nonlocal calls
        calls += 1
        return None

    assert empty() is None
    assert empty() is None
    assert calls == 1


def test_sync_exception_is_not_cached():
    calls = 0

    @func_cache()
    def flaky():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("temporary")
        return "recovered"

    with pytest.raises(ValueError, match="temporary"):
        flaky()
    assert flaky() == "recovered"
    assert flaky() == "recovered"
    assert calls == 2


def test_sync_expired_entry_refreshes_before_return_when_not_lazy():
    calls = 0

    @func_cache(ttl=1)
    def value():
        nonlocal calls
        calls += 1
        return calls

    assert value() == 1
    time.sleep(1.05)
    assert value() == 2
    assert value() == 2
    assert calls == 2


@pytest.mark.parametrize("lazy", [False, True])
def test_sync_first_concurrent_calls_share_one_execution(lazy):
    started = threading.Event()
    release = threading.Event()
    calls = 0

    @func_cache(lazy=lazy)
    def slow(value):
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(2)
        return value * 2

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(slow, 5) for _ in range(8)]
        try:
            assert started.wait(1)
            time.sleep(0.05)
            assert calls == 1
        finally:
            release.set()
        assert [future.result(timeout=2) for future in futures] == [10] * 8
    assert calls == 1


def test_sync_expired_concurrent_calls_share_one_refresh():
    started = threading.Event()
    release = threading.Event()
    calls = 0

    @func_cache(ttl=1)
    def value():
        nonlocal calls
        calls += 1
        if calls == 2:
            started.set()
            assert release.wait(2)
        return calls

    assert value() == 1
    time.sleep(1.05)
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(value) for _ in range(8)]
        try:
            assert started.wait(1)
            time.sleep(0.05)
            assert calls == 2
        finally:
            release.set()
        assert [future.result(timeout=2) for future in futures] == [2] * 8
    assert calls == 2


def test_sync_lazy_returns_stale_and_starts_only_one_refresh():
    started = threading.Event()
    release = threading.Event()
    calls = 0

    @func_cache(ttl=1, lazy=True)
    def value():
        nonlocal calls
        calls += 1
        current = calls
        if current == 2:
            started.set()
            assert release.wait(2)
        return current

    assert value() == 1
    time.sleep(1.05)
    try:
        begin = time.monotonic()
        assert value() == 1
        assert time.monotonic() - begin < 0.5
        assert started.wait(1)
        assert value() == 1
        assert calls == 2
    finally:
        release.set()

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and value() != 2:
        time.sleep(0.01)
    assert value() == 2
    assert calls == 2


def test_async_hit_and_parameter_keys():
    async def scenario():
        calls = []

        @func_cache()
        async def calculate(value):
            calls.append(value)
            return value * 2

        assert await calculate(2) == 4
        assert await calculate(2) == 4
        assert await calculate(3) == 6
        assert calls == [2, 3]

    asyncio.run(scenario())


def test_async_exception_is_not_cached():
    async def scenario():
        calls = 0

        @func_cache()
        async def flaky():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ValueError("temporary")
            return "recovered"

        with pytest.raises(ValueError, match="temporary"):
            await flaky()
        assert await flaky() == "recovered"
        assert await flaky() == "recovered"
        assert calls == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("lazy", [False, True])
def test_async_first_concurrent_calls_share_one_execution(lazy):
    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        @func_cache(lazy=lazy)
        async def slow(value):
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return value * 2

        tasks = [asyncio.create_task(slow(5)) for _ in range(8)]
        try:
            await asyncio.wait_for(started.wait(), 1)
            await asyncio.sleep(0)
            assert calls == 1
        finally:
            release.set()
        assert await asyncio.wait_for(asyncio.gather(*tasks), 2) == [10] * 8
        assert calls == 1

    asyncio.run(scenario())


def test_async_expired_concurrent_calls_share_one_refresh():
    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        @func_cache(ttl=1)
        async def value():
            nonlocal calls
            calls += 1
            if calls == 2:
                started.set()
                await release.wait()
            return calls

        assert await value() == 1
        await asyncio.sleep(1.05)
        tasks = [asyncio.create_task(value()) for _ in range(8)]
        try:
            await asyncio.wait_for(started.wait(), 1)
            await asyncio.sleep(0)
            assert calls == 2
        finally:
            release.set()
        assert await asyncio.wait_for(asyncio.gather(*tasks), 2) == [2] * 8
        assert calls == 2

    asyncio.run(scenario())


def test_async_expired_entry_refreshes_before_return_when_not_lazy():
    async def scenario():
        calls = 0

        @func_cache(ttl=1)
        async def value():
            nonlocal calls
            calls += 1
            return calls

        assert await value() == 1
        await asyncio.sleep(1.05)
        assert await value() == 2
        assert await value() == 2
        assert calls == 2

    asyncio.run(scenario())


def test_async_lazy_returns_stale_and_starts_only_one_refresh():
    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        @func_cache(ttl=1, lazy=True)
        async def value():
            nonlocal calls
            calls += 1
            current = calls
            if current == 2:
                started.set()
                await release.wait()
            return current

        assert await value() == 1
        await asyncio.sleep(1.05)
        try:
            assert await asyncio.wait_for(value(), 0.5) == 1
            await asyncio.wait_for(started.wait(), 1)
            assert await asyncio.wait_for(value(), 0.5) == 1
            assert calls == 2
        finally:
            release.set()

        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and await value() != 2:
            await asyncio.sleep(0.01)
        assert await value() == 2
        assert calls == 2

    asyncio.run(scenario())
