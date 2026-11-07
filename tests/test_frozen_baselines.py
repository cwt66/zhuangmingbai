# -*- coding: utf-8 -*-
"""锁住一件很容易"本地绿、clone 红"的事：冻结基线必须真的在仓库里。

## 这个测试是怎么来的

2026-11-07 把冻结基线从 3 份补到 5 份，本地 `check_all.sh` 全绿、五份都「与冻结基线一致」。
于是写进材料：「防回归覆盖面 3/5 → 5/5」。

直到从 GitHub 匿名 clone 跑了一次，第 8 步直接红：

    缺少基线文件，先跑一次不带 --check

原因是 `.gitignore` 里有一行 `eval/predictions/`——整个目录被忽略。
原来那三份是早于这条规则加进去的，所以还在；新补的两份生成后从没被跟踪。
**本地永远绿，别人永远红。**

这正是「自证」的典型形态：只在自己机器上成立的结论。
所以这个测试不看引擎、不看数字，只看一件事：

    DOCS 里列了几份单据，仓库里就必须有几个对应的基线文件。

`git ls-files` 而不是 `os.path.exists`——因为要防的正是「文件在本地、但没入库」。
"""
import importlib.util
import os
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAP = os.path.join(ROOT, "scripts", "snapshot_predictions.py")


def _load_snapshot_module():
    spec = importlib.util.spec_from_file_location("_snap_for_test", SNAP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tracked(pattern):
    """被 git 跟踪的文件（相对仓库根）。没装 git 或不在仓库里时返回 None。"""
    try:
        r = subprocess.run(["git", "-C", ROOT, "ls-files", pattern],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return [x for x in (r.stdout or "").splitlines() if x.strip()]


class TestFrozenBaselinesInRepo(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_snapshot_module()
        cls.slugs = [d["slug"] for d in cls.mod.DOCS]

    def test_every_spec_has_baseline_file(self):
        """DOCS 里每一份都要有对应的 eval/predictions/<slug>.json。"""
        missing = [s for s in self.slugs
                   if not os.path.isfile(os.path.join(ROOT, "eval", "predictions", s + ".json"))]
        self.assertEqual(missing, [], "缺少基线文件：%s" % missing)

    def test_every_baseline_is_tracked_by_git(self):
        """更关键的一条：文件得**入库**，不是躺在本地。

        没有这一步，`check_all.sh` 在干净 clone 里会 FAIL「缺少基线文件」，
        而我们只会在自己机器上看到五份全绿。
        """
        tracked = _tracked("eval/predictions")
        if tracked is None:
            self.skipTest("不在 git 仓库里（如离线包解压目录）")
        have = {os.path.basename(p) for p in tracked if p.endswith(".json")}
        missing = [s for s in self.slugs if s + ".json" not in have]
        self.assertEqual(missing, [],
                         "这些基线的文件存在但没入库（检查 .gitignore）：%s" % missing)

    def test_gitignore_does_not_swallow_baselines(self):
        """直接盯住那条规则：`eval/predictions/*` 必须配一条 `!*.json` 白名单。"""
        gi = os.path.join(ROOT, ".gitignore")
        if not os.path.isfile(gi):
            self.skipTest("无 .gitignore")
        with open(gi, encoding="utf-8") as f:
            lines = [x.strip() for x in f if x.strip() and not x.strip().startswith("#")]
        # 只要没有把整个目录排除的写法，就算安全
        hard = [x for x in lines if x.rstrip("/") == "eval/predictions"]
        self.assertEqual(hard, [],
                         "`.gitignore` 把整目录排除了（子文件无法用 ! 放行）：%s" % hard)
        if any(x.startswith("eval/predictions") for x in lines):
            self.assertIn("!eval/predictions/*.json", lines,
                          "忽略了 eval/predictions 却没有补白名单")


if __name__ == "__main__":
    unittest.main()
