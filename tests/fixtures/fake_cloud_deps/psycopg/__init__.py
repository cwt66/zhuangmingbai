"""CI 环境模拟桩：装了驱动，但连不上服务。

只用于 eval/determinism_matrix.py 的 `fake_cloud_deps` 变体，
通过 PYTHONPATH 注入；不是真的数据库驱动，也不联网。
"""


def connect(*args, **kwargs):
    raise OSError("fake psycopg: PG unreachable (determinism fixture)")
