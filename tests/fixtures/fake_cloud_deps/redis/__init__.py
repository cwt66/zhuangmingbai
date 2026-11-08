"""CI 环境模拟桩：装了 redis 客户端，但连不上服务。"""


class Redis:
    @staticmethod
    def from_url(*args, **kwargs):
        raise OSError("fake redis: cache unreachable (determinism fixture)")
