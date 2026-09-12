"""
tests/fake_redis.py

Lightweight async in-memory Redis simulator for automated tests.
Implements the exact subset of Redis commands used by WeatherGPT:
  - eval (atomic Lua script rate limit)
  - setex, get, delete, ttl, expire, keys
  - lrange, rpush
  - pipeline (transaction context)
  - ping
"""

import fnmatch
import time
from typing import Any, Dict, List, Optional


class FakeRedisAsync:
    """Async Redis simulator for testing without requiring a live network connection."""

    def __init__(self) -> None:
        self._data: Dict[str, Any] = {}
        self._expires: Dict[str, float] = {}
        self._lists: Dict[str, List[str]] = {}

    async def ping(self) -> bool:
        return True

    async def get(self, key: str) -> Optional[Any]:
        if key in self._expires and time.time() > self._expires[key]:
            self._data.pop(key, None)
            self._expires.pop(key, None)
            return None
        return self._data.get(key)

    async def setex(self, key: str, ttl: int, value: Any) -> bool:
        self._data[key] = value
        self._expires[key] = time.time() + ttl
        return True

    async def delete(self, *keys: str) -> int:
        count = 0
        for k in keys:
            if k in self._data or k in self._lists:
                count += 1
            self._data.pop(k, None)
            self._expires.pop(k, None)
            self._lists.pop(k, None)
        return count

    async def ttl(self, key: str) -> int:
        if key not in self._expires:
            return -1
        rem = int(self._expires[key] - time.time())
        return max(1, rem)

    async def expire(self, key: str, ttl: int) -> bool:
        self._expires[key] = time.time() + ttl
        return True

    async def lrange(self, key: str, start: int, stop: int) -> List[str]:
        lst = self._lists.get(key, [])
        if stop == -1:
            return lst[start:]
        return lst[start : stop + 1]

    async def rpush(self, key: str, *items: str) -> int:
        if key not in self._lists:
            self._lists[key] = []
        self._lists[key].extend(items)
        return len(self._lists[key])

    async def keys(self, pattern: str = "*") -> List[str]:
        all_k = set(self._data.keys()) | set(self._lists.keys())
        return [k for k in all_k if fnmatch.fnmatch(k, pattern)]

    async def eval(self, script: str, numkeys: int, key: str, limit: int, window: int) -> List[int]:
        limit_num = int(limit)
        window_sec = int(window)
        now = time.time()
        if key in self._expires and now > self._expires[key]:
            self._data.pop(key, None)
            self._expires.pop(key, None)

        curr = int(self._data.get(key, 0)) + 1
        self._data[key] = str(curr)
        if curr == 1 or key not in self._expires:
            self._expires[key] = now + window_sec
        ttl_rem = max(1, int(self._expires[key] - now))
        return [curr, ttl_rem]

    def pipeline(self, transaction: bool = True) -> Any:
        parent = self

        class Pipeline:
            def __init__(self) -> None:
                self.ops: List[Any] = []

            async def __aenter__(self) -> "Pipeline":
                return self

            async def __aexit__(self, *args: Any) -> None:
                pass

            def delete(self, *keys: str) -> None:
                self.ops.append(("delete", keys))

            def rpush(self, key: str, *items: str) -> None:
                self.ops.append(("rpush", (key, items)))

            def expire(self, key: str, ttl: int) -> None:
                self.ops.append(("expire", (key, ttl)))

            def setex(self, key: str, ttl: int, val: Any) -> None:
                self.ops.append(("setex", (key, ttl, val)))

            async def execute(self) -> List[Any]:
                results = []
                for op, args in self.ops:
                    if op == "delete":
                        results.append(await parent.delete(*args))
                    elif op == "rpush":
                        results.append(await parent.rpush(args[0], *args[1]))
                    elif op == "expire":
                        results.append(await parent.expire(args[0], args[1]))
                    elif op == "setex":
                        results.append(await parent.setex(args[0], args[1], args[2]))
                return results

        return Pipeline()
