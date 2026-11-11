# -*- coding: utf-8 -*-
"""上传链路判据：/followup 的两版上传，每个拒绝分支都要给出**正确的理由**。

为什么要单独一个脚本：`src/followup.py --self-test` 测的是判定逻辑，测不到
FastAPI 的上传处理。而「返回 400」不等于「正确拒绝」——如果所有错误都被
`except Exception` 兜成 500，或者守门被删掉，状态码看起来仍然是 4xx/5xx，
但用户拿不到能行动的信息。

不装 httpx / TestClient：那会往证据的 `resolvedDependencies` 里加东西。
直接构造 `starlette.datastructures.UploadFile` 调异步处理函数，零新增依赖。
"""
import argparse
import asyncio
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))


def _up(name, data):
    from starlette.datastructures import UploadFile
    return UploadFile(io.BytesIO(data), filename=name)


def _h2(body):
    m = re.search(r"<h2>(.*?)</h2>", body, re.S)
    return m.group(1).strip() if m else ""


def _body(resp):
    return resp.body.decode("utf-8")


def self_test():
    import main

    before = open(os.path.join(main.pipeline.DATA, "real", "feitai_budget.csv"), "rb").read()
    after = open(os.path.join(main.pipeline.DATA, "samples", "feitai_change_demo.csv"), "rb").read()
    big = b"x" * (4 * 1024 * 1024 + 1)
    cases = [
        ("正常两版", [("原版.csv", before), ("新版.csv", after)], "old_house", 200, "结果页"),
        ("新房模板", [("a.csv", before), ("b.csv", after)], "new_house", 200, "结果页"),
        ("只传一份", [("a.csv", before)], "old_house", 400, "需要两份报价"),
        ("非法模板", [("a.csv", before), ("b.csv", after)], "bad", 400, "模板不合法"),
        ("空文件", [("a.csv", b""), ("b.csv", after)], "old_house", 400, "空文件"),
        ("超 4MB", [("a.csv", big), ("b.csv", after)], "old_house", 400, "文件过大"),
        ("非报价文档", [("a.pdf", b"not a quote at all"), ("b.csv", after)],
         "old_house", 400, "文档类型不符"),
    ]

    async def run():
        out = []
        for tag, files, tk, want_code, want_title in cases:
            ups = [_up(n, d) for n, d in files]
            r = await main.followup_upload(files=ups, template_key=tk)
            out.append((tag, r, want_code, want_title))
        return out

    results = []
    for tag, r, want_code, want_title in asyncio.run(run()):
        body = _body(r)
        title = _h2(body)
        if want_title == "结果页":
            ok = (r.status_code == 200 and "落实率" in body
                  and "闭水试验" in body and 'action="/followup"' in body)
            detail = "status=%s 落实率=%s 表单=%s" % (
                r.status_code, "落实率" in body, 'action="/followup"' in body)
        else:
            ok = r.status_code == want_code and title == want_title
            detail = "status=%s h2=%s（期望 %s / %s）" % (
                r.status_code, title, want_code, want_title)
        results.append({"name": tag, "status": "pass" if ok else "fail", "detail": detail})

    # 演示页必须也带上传入口，否则用户看完演示不知道去哪传自己的两版
    g = main.followup_page()
    ok = g.status_code == 200 and 'action="/followup"' in _body(g)
    results.append({"name": "演示页含上传入口",
                    "status": "pass" if ok else "fail",
                    "detail": "status=%s" % g.status_code})

    failed = [x for x in results if x["status"] != "pass"]
    return {"self_test": "PASS" if not failed else "FAIL",
            "checks": len(results), "passed": len(results) - len(failed),
            "results": results, "violations": [x["name"] for x in failed]}


def main(argv=None):
    ap = argparse.ArgumentParser(description="落实核对上传链路自测")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not a.self_test:
        ap.error("目前只支持 --self-test")
    out = self_test()
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        for r in out["results"]:
            print("  %s %s%s" % ("✓" if r["status"] == "pass" else "✗", r["name"],
                                 ("  ← " + r["detail"]) if r["status"] != "pass" else ""))
        print("%s 上传链路断言 %d/%d" % (out["self_test"], out["passed"], out["checks"]))
    return 0 if out["self_test"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
