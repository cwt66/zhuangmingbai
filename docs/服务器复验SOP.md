# 服务器侧复验 / 打包 SOP（本机不跑）

> 2026-11-10 起：本机只做代码与文档编辑、git 提交与静态文件发布；
> **测试、自检、构建、渲染、部署一律在服务器上跑**。
> 下面的命令都写成「在服务器上执行」，本机不执行。

## 0. 前提

- 服务器有 Python 3.10+（3.10/3.11/3.12/3.13 都验过）、git、网络。
- 判定引擎零第三方包；`check_all.sh` 的 20 步里只有 Playwright 渲染
  （评委复验页 PDF）需要额外依赖，且**不在自检里**——PDF 是单独渲的。
- 如果要重渲 PDF，另装 Playwright + Chromium；不重渲就不装。

## 1. 拉代码（干净 clone，别用本地工作区）

```bash
git clone https://github.com/cwt66/zhuangmingbai.git zmb
cd zmb
git rev-parse --short HEAD
```

## 2. 全量复验（服务器上跑）

```bash
bash scripts/check_all.sh          # 20 步；期望 [ALL GREEN]
python3 -m unittest discover -s tests -v | tail -5
```

关键判据单独跑：

```bash
python3 scripts/claim_ledger.py --self-test --json      # 25 条 / 0 违规
python3 eval/ledger_chain.py --json                     # 链 7/7、包 4/4、Merkle 12/12
python3 scripts/verify_skill_spec.py --self-test        # 20/20
python3 scripts/verify_showcase.py --self-test --json   # 11 页 / 24 项
python3 scripts/verify_package_numbers.py --json        # 提交包数字一致性
python3 scripts/verify_clean_clone.py --json            # 干净 clone 再跑一遍
python3 scripts/ledger.py verify --json                 # 账本链 + 锚点 + merkle_root
```

## 3. 公开锚点对照（服务器上跑，需要联网）

```bash
python3 scripts/publish_pages.py --check --json         # 页面 + 摘要表全字段 + 根自洽
python3 scripts/make_bundle.py --csv data/real/feitai_budget.csv \
    --label 翡翠台 --out /tmp/x.zmb.zip
python3 scripts/verify_bundle.py --zip /tmp/x.zmb.zip --run-l1 \
    --anchor-url https://cwt66.github.io/zhuangmingbai/verify/anchor.json
```

## 4. 重建交付物（服务器上跑）

本机这轮**没有**重打离线包 / 提交包；交付目录里的两个 zip 仍是上一版快照，
`文件校验_sha256.txt` 与它们一致（自洽，但不含本轮新增文档）。
在服务器上重建：

```bash
python3 scripts/build_offline_zip.py \
    --out /path/to/国奖提交包/06_开源与部署/装明白_离线演示包.zip

# 提交包：把 国奖提交包/ 整个目录打成 zip（排除 .DS_Store / __pycache__ / .git / .render_tmp）
# 然后按实际字节重算 sha256，写回 06_开源与部署/文件校验_sha256.txt
# 和 deliverables/README.md 的四行 sha256
python3 scripts/verify_package_numbers.py --json    # 重建后必须 PASS
```

重渲 11 页 PDF（需要 Playwright）：

```bash
cd /path/to/国奖提交包/02_作品介绍PPT
python3 render_verify_pdf.py
```

## 5. 发布展示页与公开锚点（服务器上跑，需要推送权限）

```bash
export GIT_SSH_COMMAND='ssh -i ~/.ssh/<deploy_key> -p 443 -o IdentitiesOnly=yes'
python3 scripts/publish_pages.py            # 同步 index.html / PDF / anchor.json
sleep 90
python3 scripts/publish_pages.py --check --json
```

## 6. 本机做了什么、没做什么

| 动作 | 本机 | 服务器 |
|---|---|---|
| 改代码 / 文档 / 数据 | ✅ | — |
| git commit + push 双仓 | ✅ | — |
| 静态展示页与 anchor.json 发布 | ✅（轻量 git 操作） | 可重跑 |
| `unittest` / `check_all.sh` / 各类判据 | ❌ 不再跑 | ✅ |
| 重打离线包 / 提交包 zip | ❌ 未重打 | ✅ |
| Playwright 重渲 PDF | 上一轮已完成 | 可重跑 |
| 启动 Web 服务 / Demo | ❌ | ✅ |

> 边界：GitHub Actions 的 7 个 job 是云端 CI，不算「本地跑」；
> 推送后它会自动复验 20 步、干净 clone、4 个 Python 版本、aarch64。
