# -*- coding: utf-8 -*-
"""Redis 能力层：缓存 / 限流 / 任务状态。没有 Redis 时自动降级为进程内实现。

在架构里的位置
--------------
VLM 解析慢（3–8 秒/张），不能同步阻塞请求；同一份单据的审计结果也不该重复计算。
所以 Redis 承担三件具体的事，而不是"为了用 Redis 而用"：

1. **审计结果缓存**：key = 单据内容 sha256 + 模板 + 范围，TTL 1 小时；
2. **限流**：按来源 IP 的滑动窗口，防止公网 Demo 被刷；
3. **任务状态**：异步解析任务的状态机（queued/running/done/failed）放 Redis，
   多副本部署时任何一台机器都能读到；执行器在进程内，换成独立 worker 时接口不变。

降级策略：`REDIS_URL` 未配置或连接失败 → 进程内 TTL 字典 + 锁。
公网 Demo 不能因为 Redis 挂了就打不开，但 /health 必须如实显示当前用的是哪一个。
"""
import json
import os
import threading
import time
import uuid

DEFAULT_TTL = 3600


class Cache:
    def __init__(self, url=None):
        self.url = (url if url is not None else os.environ.get("REDIS_URL", "")).strip()
        self.kind = "memory"
        self.degraded = None
        self._data = {}
        # RLock：rate_limit / acquire 会在持锁时调用 _mem_get/_mem_set（也会拿锁）。
        # 用普通 Lock 会自锁死——测试会把这种问题抓出来，线上就是整个 Demo 卡住。
        self._lock = threading.RLock()
        self._client = None
        if self.url:
            try:
                import redis  # 可选依赖
                self._client = redis.Redis.from_url(
                    self.url, socket_timeout=1.5, socket_connect_timeout=1.5,
                    decode_responses=True)
                self._client.ping()
                self.kind = "redis"
            except Exception as e:  # noqa: BLE001
                self.degraded = "Redis 不可用，已降级内存：%s" % e
                self._client = None

    # ---------------------------------------------------------------- basic
    def _mem_get(self, key):
        with self._lock:
            item = self._data.get(key)
            if not item:
                return None
            value, expire = item
            if expire and expire < time.time():
                self._data.pop(key, None)
                return None
            return value

    def _mem_set(self, key, value, ttl):
        with self._lock:
            self._data[key] = (value, time.time() + ttl if ttl else 0)

    def get_json(self, key):
        try:
            raw = self._client.get(key) if self.kind == "redis" else self._mem_get(key)
        except Exception as e:  # noqa: BLE001
            self.degraded = "Redis get 失败，已降级内存：%s" % e
            self.kind = "memory"
            self._client = None
            raw = self._mem_get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except Exception:  # noqa: BLE001
            return None

    def set_json(self, key, value, ttl=DEFAULT_TTL):
        raw = json.dumps(value, ensure_ascii=False, default=str)
        try:
            if self.kind == "redis":
                self._client.setex(key, int(ttl), raw)
            else:
                self._mem_set(key, raw, ttl)
        except Exception as e:  # noqa: BLE001
            self.degraded = "Redis set 失败，已降级内存：%s" % e
            self.kind = "memory"
            self._client = None
            self._mem_set(key, raw, ttl)
        return True

    # ---------------------------------------------------------------- rate limit
    def rate_limit(self, bucket, limit=30, window=60):
        """固定窗口计数。返回 (allowed, remaining, reset_seconds)。"""
        key = "rl:%s:%d" % (bucket, int(time.time() // window))
        try:
            if self.kind == "redis":
                n = self._client.incr(key)
                if n == 1:
                    self._client.expire(key, int(window) + 2)
                return n <= limit, max(0, limit - n), int(window)
            with self._lock:
                n = int(self._mem_get(key) or 0) + 1
                self._mem_set(key, str(n), window + 2)
                return n <= limit, max(0, limit - n), int(window)
        except Exception as e:  # noqa: BLE001
            self.degraded = "Redis 限流失败，已降级内存：%s" % e
            self.kind = "memory"
            self._client = None
            with self._lock:
                n = int(self._mem_get(key) or 0) + 1
                self._mem_set(key, str(n), window + 2)
                return n <= limit, max(0, limit - n), int(window)

    # ---------------------------------------------------------------- job status
    def job_set(self, job_id, state, ttl=1800):
        return self.set_json("job:%s" % job_id, state, ttl=ttl)

    def job_get(self, job_id):
        return self.get_json("job:%s" % job_id)

    def new_job_id(self):
        return "J-" + uuid.uuid4().hex[:10]

    # ---------------------------------------------------------------- lock
    def acquire(self, key, ttl=30):
        """最简 SET NX 锁；只为避免同一份单据被并发重复解析。"""
        try:
            if self.kind == "redis":
                return bool(self._client.set("lock:%s" % key, "1", nx=True, ex=int(ttl)))
        except Exception:  # noqa: BLE001
            pass
        k = "lock:%s" % key
        with self._lock:
            if self._mem_get(k):
                return False
            self._mem_set(k, "1", ttl)
            return True

    def release(self, key):
        try:
            if self.kind == "redis":
                self._client.delete("lock:%s" % key)
                return
        except Exception:  # noqa: BLE001
            pass
        with self._lock:
            self._data.pop("lock:%s" % key, None)

    def health(self):
        # 没配 REDIS_URL 时是「未启用」，不是「故障」；配了但连不上才是 degraded。
        if self.kind == "redis":
            return {"status": "ok", "kind": "redis", "configured": True, "degraded": None}
        return {"status": "degraded" if self.url else "ok",
                "kind": "memory", "configured": bool(self.url),
                "degraded": self.degraded or ("未配置 REDIS_URL，使用进程内缓存" if not self.url else None)}


_shared = None
_shared_lock = threading.Lock()


def get_cache():
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = Cache()
        return _shared
