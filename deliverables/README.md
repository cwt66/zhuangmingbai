# 交付物（可公开部分）

> 这里放的是**评委可直接下载的最终产物**。工作文件夹里其余内容（真实单据原图、
> 官方报名表、过程稿、第三方仓库副本、历史 zip）不进公开仓，
> 逐项理由见 `docs/GitHub上传范围与脱敏边界.md`。

| 文件 | 内容 | sha256 |
|---|---|---|
| `装明白_国奖提交包_2026-11-07.zip` | 11 分区完整提交包（技术文档 / 答辩材料 / 评测证据 / 离线包 / 检查表） | `af66b7ef03117cfb0bc634b67f0f46dded086c93e1a2719fd83ad94669ad6068` |
| `装明白_离线演示包.zip` | 纯代码离线快照，含 18 步自检所需全部文件 | `96683304e99878f4636afe5e532dcc33a4648d51c27d363838d9602ef3956127` |

生成时点：commit `2f7fc0d`（2026-11-08）。
两个 zip 的文本层已做 PII 扫描；报名信息栏经人工核对均为方括号占位符。

## 自己核对

```bash
shasum -a 256 deliverables/*.zip
# 与上表逐位比对
python3 scripts/verify_package_numbers.py --json   # 提交包数字一致性
python3 scripts/pii_scan.py --strict               # 代码仓隐私扫描
```

## 为什么要单独放一份

`国奖提交包/` 在代码仓**外面**，GitHub Actions 的 checkout 里没有它，
所以 `scripts/verify_package_numbers.py` 只能在本机查。
把最终 zip 放进仓库，等于让**评委拿到手的那份材料本身**也在 GitHub 上可下载、可校验；
而材料里每句数字仍由仓库内的命令复算，两份真相不并存——
`deliverables/` 只是发布快照，不是第二份源文档。
