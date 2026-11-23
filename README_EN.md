# Zhuang Mingbai — Live Demo

**http://113.47.4.91:8000** (available during review; `/health` green: PostgreSQL + Redis + MaaS)

# Zhuang Mingbai — Computable Audit for Renovation Quotes

> 11th Huawei ICT Competition · China Innovation Contest · Track 1: AI Applications on Huawei Cloud

**The problem is not that renovation quotes are hard to read — it is that there is no coordinate system.**
We build one: 119 standard work items, 388 aliases, and 13 reviewable rules. Every finding points back to
the original spreadsheet row, the rule that fired, and the calculation behind it.

- Verification page: <https://cwt66.github.io/zhuangmingbai/verify/>
- Source (MIT): <https://github.com/cwt66/zhuangmingbai> · <https://gitee.com/Cwt66/zhuangmingbai>

## What it does

Upload 2–4 quotes (CSV / XLSX / PDF / photo) before signing a contract. The report answers:

- What work items are **missing** compared with a scoped template?
- What is **billed twice**, under different names or units?
- What happens to the real cost after adding the missing items?

**Boundary:** we do not claim a quote is "overpriced". We check omissions, duplicates, unit/scope
consistency and arithmetic. Low-evidence findings never enter the deduction amount.

## Why this is not "another LLM reading a document"

| | Typical LLM approach | This project |
|---|---|---|
| Finding provenance | model-generated text | source row + rule ID + formula + evidence score |
| Repeatability | may differ across runs | byte-identical decision fingerprint (12 environment variants) |
| Low confidence | mixed into conclusions | routed to a confirmation list, never into money |
| Reproduction | prompt + model access | 344 tests, 20-step self-check, offline bundle |

The LLM is used only for understanding and expression. Judgement runs in deterministic rules.

## Evidence (all reproducible)

| Item | Value |
|---|---|
| Real documents | 5 quotes / 603 line items + 11 summary rows |
| Gold labels | 2 fully annotated documents, Wilson 95% CI reported; **existence proof only**, no population recall claim |
| Injection suite | 242/242 known injected cases detected (lower-bound check, not recall) |
| Determinism | identical fingerprint across 12 variants; arm64 ↔ x86_64 comparison archived |
| Tests | 344 unit tests, 20-step `check_all.sh` `[ALL GREEN]` |
| Audit package | hash chain + Merkle inclusion proof, verifiable offline |

## Huawei Cloud integration

CodeArts CodeAgent was used across all five stages (requirements, code generation, build, fix, deploy).
The application integrates RDS PostgreSQL, DCS Redis, ModelArts Studio MaaS and OBS with documented
local fallbacks; ECS + Nginx hosted the public demo (7 routes returned 200 during the test window).
`/health` reports the actual backend in use — code integration is never presented as an active instance.

## Quick start

```bash
git clone https://github.com/cwt66/zhuangmingbai.git
cd zhuangmingbai
python3 -m pip install -r requirements.txt
python3 -m uvicorn main:app --host 127.0.0.1 --port 8000
# /demo sample quotes · /real real-document regression · /history audit history
# /review correction desk · /perceive photo intake · /demo.pdf shareable report
bash scripts/check_all.sh          # 20 steps, expected [ALL GREEN]
```

No network, no cloud account: unzip `装明白_离线演示包.zip` from the release and run `run_offline.sh`.

## License and data

MIT (see [LICENSE](./LICENSE)); usage boundaries in [NOTICE.md](./NOTICE.md). Real quotes are public
renovation diaries used only for regression testing; sample amounts are recreated. This software is not
legal, financial or engineering advice.
