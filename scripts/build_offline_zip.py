# -*- coding: utf-8 -*-
"""打离线演示包：git 跟踪的全部文件 → zip（含中文名 UTF-8 标志位）。

为什么不用 `git archive`：
1. `git archive` 打出的中文文件名在 Windows 资源管理器里会乱码（未置 UTF-8 标志位 bit 11）；
2. 但它至少不会漏文件——真正的坑在 `git ls-files` 默认把中文路径转义成八进制
   （`"docs/\\344\\275\\277\\347\\224\\250..."`），打包脚本会**静默跳过**这些文件。
   第一次打出来只有 32 个，`docs/使用文档.md` 就丢了，靠核对清单才发现。

所以：`git -c core.quotepath=false ls-files -z` + Python zipfile 逐条写 UTF-8 标志位。

用法：
    python3 scripts/build_offline_zip.py
    python3 scripts/build_offline_zip.py --out ../装明白_校赛材料/装明白_离线演示包.zip
"""
import argparse
import os
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(os.path.dirname(ROOT), "装明白_校赛材料", "装明白_离线演示包.zip")

# 不进包：太大了或与"能跑起来"无关
# 预测快照要进包：评委可以拿它比对指纹，验证结论没被事后改过
EXCLUDE_PREFIX = (".git/", ".auto-deploy/", "__pycache__/")


def tracked_files():
    raw = subprocess.run(["git", "-c", "core.quotepath=false", "ls-files", "-z"],
                         cwd=ROOT, capture_output=True).stdout
    return [f.decode("utf-8") for f in raw.split(b"\x00") if f]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()

    files = tracked_files()
    skipped, written = [], 0
    if os.path.exists(args.out):
        os.remove(args.out)

    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            if f.startswith(EXCLUDE_PREFIX):
                skipped.append(f)
                continue
            full = os.path.join(ROOT, f)
            if not os.path.isfile(full):
                skipped.append(f + "（不存在）")
                continue
            zi = zipfile.ZipInfo("zhuangmingbai/" + f)
            zi.flag_bits |= 0x800          # UTF-8 文件名标志，Windows 才不乱码
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = (0o755 if f.endswith(".sh") else 0o644) << 16
            with open(full, "rb") as fh:
                z.writestr(zi, fh.read())
            written += 1

    size_kb = round(os.path.getsize(args.out) / 1024)
    with zipfile.ZipFile(args.out) as z:
        bad = z.testzip()
        names = z.namelist()
    print("写入 %d 个文件，%d KB → %s" % (written, size_kb, args.out))
    print("完整性：", "OK" if bad is None else "损坏：" + bad)
    if skipped:
        print("跳过 %d 个：%s" % (len(skipped), skipped[:5]))

    # 关键文件自检：这几个缺任何一个，包就是残的
    must = ["LICENSE", "run_offline.sh", "docs/使用文档.md", "main.py", "requirements.txt",
            "data/real/sh_zhuoguan_halfpack.csv", "data/real/huaxibeian_newhouse.csv",
            "data/coverage_v0.json", "scripts/snapshot_predictions.py", "scripts/eval_audit.py",
            "src/perception.py", "scripts/parse_doc.py", "tests/test_regression.py",
            "eval/predictions/huaxibeian.json"]
    miss = [k for k in must if not any(n.endswith(k) for n in names)]
    print("关键文件：", "全在" if not miss else "缺 " + str(miss))
    return 1 if (miss or bad) else 0


if __name__ == "__main__":
    sys.exit(main())
