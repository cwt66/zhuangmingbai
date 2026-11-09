# 交付物（可公开部分）

> 这里放的是**评委可直接下载的最终产物**。工作文件夹里其余内容（真实单据原图、
> 官方报名表、过程稿、第三方仓库副本、历史 zip）不进公开仓，
> 逐项理由见 `docs/GitHub上传范围与脱敏边界.md`。

| 文件 | 内容 | sha256 |
|---|---|---|
| `装明白_国奖提交包_2026-11-07.zip` | 11 分区完整提交包（技术文档 / 答辩材料 / 评测证据 / 离线包 / 检查表） | `09ad5dd764b6ec0156c94425e5783ceff1b921fd6ee2bac975ea3107a13fd7cc` |
| `装明白_离线演示包.zip` | 纯代码离线快照，含 19 步自检所需全部文件（刻意排除 deliverables/ 防递归） | `61cae8bed6d10596032b155c174e37e2b32ded6f2013e427db3b7a6a0033747a` |
| `装明白_评委复验页.html` | **当前版 11 页**，单文件零依赖，双击即用；数字与台账绑定 | `290bbd38b0023a46cb34a6d8a13e2a2d30ebfe256f5787983c28afa70102465b` |
| `装明白_评委复验页_11页.pdf` | 同一份 HTML 逐页渲染；11 张截图互不相同才落盘 | `97b1f73e7794e86c9ebda7418e72d3387fa6f13a64e51ca3cb4d4d4367ad282d` |

生成时点：commit `f3d2de5`（2026-11-08）。
两个 zip 的文本层已做 PII 扫描；报名信息栏经人工核对均为方括号占位符。

> 本目录是 **f3d2de5 时点的发布快照**。之后若出现 CI 修复类 commit，
> 交付内容不变；代码以仓库 HEAD 为准，两者关系由数字一致性判据记录。

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
