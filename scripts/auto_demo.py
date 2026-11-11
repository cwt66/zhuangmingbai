#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""演示即代码：把 3 分钟演示视频的 8 个镜头写成可复跑的剧本。

## 为什么

校赛要求提交演示视频，但手录有三个问题：录一次要 90 分钟、每次改版都要重录、
评委无法验证「你演示的和你仓库里的是同一个东西」。

VHS（charmbracelet/vhs）把终端演示变成 `.tape`；这个脚本把**网页演示**变成剧本：
一条命令跑完 8 镜，每镜产出截图 + 关键文本断言 + 结构化时间轴。
改代码后重跑，镜头自动更新；评委也能复跑验证。

## 对标（borrowed）

- `charmbracelet/vhs`：demo as code，脚本可 review、可复现
- `AlexKay28/clickcast`：JSON sidecar + **distilled assertions**——
  只比 step 数 / 每步状态 / 关键文本，不比像素、不比时间戳
- Playwright：`screenshot` / `record_video` / `get_by_text`

## 三种模式

    python3 scripts/auto_demo.py --run --spawn     # 起临时服务、跑 8 镜、出图与时间轴
    python3 scripts/auto_demo.py --verify          # 重跑并与基线比对结构（可换机）
    python3 scripts/auto_demo.py --self-test       # 静态双向断言，不开浏览器

## 边界（不掩饰）

- `--verify` **不比对像素**：字体与抗锯齿在不同机器上必然有差异。
  截图 sha256 只作为「本次运行」的证据写进时间轴，复算时忽略。
- `--run` 需要 Playwright + Chromium；服务器默认没有，所以这是本机制作动作。
- 不自动改任何业务代码；只读页面、只截屏。
"""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out" / "demo"
BASELINE = ROOT / "data" / "demo_timeline_v0.json"

# 8 镜与 `国奖提交包/03_演示视频/3分钟演示脚本_v4.md` 对齐。
# `must` 是每镜的硬断言：页面正文必须包含这些词，否则该镜判 fail。
# `scroll` 是相对滚动像素，用于把关键区域带进画面。
STEPS = (
    {"id": "s01", "name": "封面 / 上传入口", "path": "/", "scroll": 0,
     "must": ("装明白 · 可计算审计", "上传报价单")},
    {"id": "s02", "name": "样例结构与字段", "path": "/", "scroll": 0,
     # 首页一屏就装得下，scrollTo 会被 clamp 到 0；改成滚到样例表并高亮，
     # 否则 s01/s02 截出来是同一张图（实测 sha256 完全相同）。
     "js": ("const t=document.querySelector('table.s');"
            "if(t){t.style.outline='3px solid #E8A33D';t.style.outlineOffset='10px';"
            "t.scrollIntoView({block:'center'});}"),
     "must": ("样例 A", "样例 B", "漏 17 项")},
    {"id": "s03", "name": "三家表面报价", "path": "/demo", "scroll": 0,
     # 用页面上真实存在的硬信息做断言（金额比「三家公司」这种措辞稳）
     "must": ("3 家报价", "86,498", "59,902")},
    {"id": "s04", "name": "B 的 17 项漏项", "path": "/demo", "scroll": 900,
     "must": ("漏项", "基准")},
    {"id": "s05", "name": "真实成本反转", "path": "/demo", "scroll": 1700,
     "must": ("真实成本",)},
    {"id": "s06", "name": "证据三档与审计编号", "path": "/demo", "scroll": 2600,
     "must": ("证据",)},
    {"id": "s07", "name": "照片识别与校对", "path": "/perceive", "scroll": 0,
     "must": ("识别", "校对")},
    # 镜 8 用仓库里的架构图（视频脚本要的是「架构 + 云底座」，不是 health 的 JSON）
    {"id": "s08", "name": "架构与云底座", "file": "docs/assets/deck-architecture.png",
     "must": ()},
)

VIEWPORT = {"width": 1920, "height": 1080}


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def norm_text(s: str) -> str:
    """把正文压成可比对的关键词集合：去空白、去标点噪声。"""
    return re.sub(r"\s+", " ", s).strip()


def distilled(timeline):
    """只保留跨机器稳定的字段：镜头数、每镜 id/路径/状态/命中关键词。"""
    return {
        "steps": [
            {"id": s["id"], "path": s["path"], "status": s["status"],
             "hits": sorted(s["hits"])}
            for s in timeline["steps"]
        ]
    }


def wait_health(base, timeout=40.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    return False


def spawn_server(port):
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = "http://127.0.0.1:%d" % port
    if not wait_health(base):
        proc.terminate()
        raise SystemExit("✗ 临时服务未能在 40s 内就绪：%s" % base)
    return proc, base


def run_steps(base, record=False):
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("s*.png"):
        old.unlink()
    steps_out = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport=VIEWPORT, device_scale_factor=1,
                                  record_video_dir=str(OUT / "video") if record else None,
                                  record_video_size=VIEWPORT if record else None)
        page = ctx.new_page()
        for st in STEPS:
            t0 = time.time()
            source = st.get("path") or st.get("file")
            if st.get("file"):
                page.goto((ROOT / st["file"]).as_uri(), wait_until="load", timeout=30000)
            else:
                page.goto(base + st["path"], wait_until="load", timeout=30000)
            page.wait_for_timeout(250)
            if st.get("scroll"):
                page.evaluate("window.scrollTo(0, %d)" % st["scroll"])
                page.wait_for_timeout(350)
            if st.get("js"):
                page.evaluate(st["js"])
                page.wait_for_timeout(450)
            shot = OUT / (st["id"] + ".png")
            page.screenshot(path=str(shot), full_page=False)
            body = norm_text(page.inner_text("body"))
            must = tuple(st.get("must") or ())
            hits = [k for k in must if k in body]
            steps_out.append({
                "id": st["id"],
                "name": st["name"],
                "path": source,
                "status": "ok" if len(hits) == len(must) else "fail",
                "must": list(must),
                "hits": hits,
                "missing": [k for k in must if k not in body],
                "screenshot": str(shot.relative_to(ROOT)),
                "screenshot_sha256": sha256_bytes(shot.read_bytes()),
                "screenshot_bytes": shot.stat().st_size,
                "elapsed_ms": int((time.time() - t0) * 1000),
            })
            print("  [%s] %-22s %s  %s" % (
                st["id"], st["name"], "OK" if steps_out[-1]["status"] == "ok" else "FAIL",
                ("缺 " + "、".join(steps_out[-1]["missing"])) if steps_out[-1]["missing"] else ""))

        if record:
            video = page.video
            ctx.close()          # 必须先关上下文，webm 才会落盘
            browser.close()
            if video:
                dst = OUT / "demo.webm"
                shutil.move(video.path(), dst)
                print("  录制：%s（%.1f MB）" % (dst, dst.stat().st_size / 1048576))
        else:
            ctx.close()
            browser.close()

    return {
        "schema": "zmb.demo-timeline/v1",
        "viewport": VIEWPORT,
        "base_url": base,
        "steps": steps_out,
        "summary": {
            "steps": len(steps_out),
            "ok": sum(1 for s in steps_out if s["status"] == "ok"),
            "fail": sum(1 for s in steps_out if s["status"] != "ok"),
            "total_ms": sum(s["elapsed_ms"] for s in steps_out),
        },
    }


def cmd_run(args):
    base = args.base_url
    proc = None
    if args.spawn:
        proc, base = spawn_server(args.port)
    elif not wait_health(base, timeout=5):
        raise SystemExit("✗ %s 不可达。加 --spawn 让脚本起临时服务。" % base)
    try:
        tl = run_steps(base, record=args.record)
    finally:
        if proc:
            proc.terminate()
            proc.wait(timeout=10)
    # 时间轴里不写墙钟：墙钟会让同一份剧本每次运行都不同，无法复算
    tl.pop("base_url", None)
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(tl, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("✓ 时间轴 %s（%d/%d 镜通过）" % (out, tl["summary"]["ok"], tl["summary"]["steps"]))
    return 0 if tl["summary"]["fail"] == 0 else 1


def cmd_verify(args):
    if not BASELINE.exists():
        raise SystemExit("✗ 缺基线 %s，先跑 --run 并提交" % BASELINE)
    base_tl = json.loads(BASELINE.read_text(encoding="utf-8"))

    proc = None
    base = args.base_url
    if args.spawn:
        proc, base = spawn_server(args.port)
    elif not wait_health(base, timeout=5):
        raise SystemExit("✗ %s 不可达。加 --spawn。" % base)
    try:
        now = run_steps(base, record=False)
    finally:
        if proc:
            proc.terminate()
            proc.wait(timeout=10)

    a, b = distilled(base_tl), distilled(now)
    diffs = []
    if len(a["steps"]) != len(b["steps"]):
        diffs.append("镜头数 %d → %d" % (len(a["steps"]), len(b["steps"])))
    for x, y in zip(a["steps"], b["steps"]):
        if x != y:
            diffs.append("%s：%s → %s" % (x["id"], x, y))
    print("%s 结构比对：%d 镜 / 差异 %d 项" %
          ("✓ PASS" if not diffs else "✗ FAIL", len(b["steps"]), len(diffs)))
    for d in diffs[:10]:
        print("  - " + d)
    return 0 if not diffs else 1


def self_test():
    """静态双向断言：剧本结构必须完整，篡改基线必须被 verify 的比对逻辑抓到。"""
    results = []

    def case(name, ok):
        results.append({"name": name, "status": "pass" if ok else "fail"})

    ids = [s["id"] for s in STEPS]
    case("8 个镜头", len(STEPS) == 8)
    case("镜头 id 唯一", len(set(ids)) == len(ids))
    case("每镜有画面来源（路由或文件）",
         all((s.get("path", "").startswith("/") or s.get("file")) for s in STEPS))
    case("每镜有名称与断言字段", all(s["name"] and "must" in s for s in STEPS))

    # 故障注入：基线被篡改（删一镜 / 改状态 / 改关键词），distilled 必须不同
    fake = {"steps": [{"id": s["id"], "path": s.get("path") or s.get("file"),
                       "status": "ok", "hits": list(s.get("must") or ())}
                      for s in STEPS]}
    good = distilled(fake)
    case("干净基线自比对一致", good == distilled(fake))

    import copy
    broken = copy.deepcopy(good)
    broken["steps"] = broken["steps"][:-1]
    case("删一镜必须被抓到", broken != good)

    broken2 = copy.deepcopy(good)
    broken2["steps"][0]["status"] = "fail"
    case("改状态必须被抓到", broken2 != good)

    broken3 = copy.deepcopy(good)
    broken3["steps"][2]["hits"] = broken3["steps"][2]["hits"][:-1]
    case("少一个关键词必须被抓到", broken3 != good)

    ok = all(r["status"] == "pass" for r in results)
    return {"self_test": "PASS" if ok else "FAIL", "checks": len(results),
            "passed": sum(r["status"] == "pass" for r in results), "results": results}


def cmd_self_test(args):
    r = self_test()
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print("演示剧本双向断言 %s（%d/%d）" % (r["self_test"], r["passed"], r["checks"]))
        for x in r["results"]:
            print("  [%s] %s" % (x["status"], x["name"]))
    return 0 if r["self_test"] == "PASS" else 1


def main():
    ap = argparse.ArgumentParser(description="演示即代码：8 镜可复跑剧本")
    ap.add_argument("--run", action="store_true", help="跑 8 镜并写时间轴")
    ap.add_argument("--verify", action="store_true", help="与基线比对结构")
    ap.add_argument("--self-test", action="store_true", help="静态双向断言")
    ap.add_argument("--spawn", action="store_true", help="自己起临时 uvicorn")
    ap.add_argument("--port", type=int, default=8010)
    ap.add_argument("--base-url", default="http://127.0.0.1:8010")
    ap.add_argument("--record", action="store_true", help="同时录 webm")
    ap.add_argument("--out", default="out/demo_timeline.json")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.self_test:
        return cmd_self_test(a)
    if a.run:
        return cmd_run(a)
    if a.verify:
        return cmd_verify(a)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
