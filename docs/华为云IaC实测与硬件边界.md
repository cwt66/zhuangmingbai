# 华为云 IaC 实测与硬件边界

> 用途：把「与华为软硬件的关系」里最容易被当成形容词的那部分，换成有时间戳、
> 有错误码、有 state 文件的事实。
> 口径：2026-11-07 ｜ 证据来自本机 `.auto-deploy/`（**本地目录，已 gitignore，不随公开仓库发布**）

---

## 一、为什么补这份文档

此前材料里关于 ECS 只有一句「配置齐全，受余额限制未长期在线」。
这句话不假，但**没有信息量**——谁都能写一个 Dockerfile 然后不部署。

实际上项目做过 4 次真实的华为云 Terraform `apply`，
**跑到了创建 ECS 那一步，并且真的在账号里落下了 7 类云资源**。
失败原因是一个可以复述的错误码，不是「没做」。
把这段写清楚，「云原生」才从形容词变成可核对的事件。

同时它解决一个更重要的边界问题：
**这批实测用的是 x86 机型，不是鲲鹏**。
把 ARM64 开发机上的验证混写成「已适配鲲鹏」，是这份材料最需要防的一处夸大。

---

## 二、实测记录（脱敏）

| 项 | 值 |
|---|---|
| IaC 工具 | Terraform ≥ 1.9.0 + `huaweicloud/huaweicloud` provider `~> 1.90.0` |
| Provider 二进制 | `darwin_arm64`（在本机 ARM64 开发机上跑的 Terraform） |
| Region / AZ | `cn-north-4` / `cn-north-4a` |
| 申请机型 | **`s6.large.2`（通用计算型，x86）** ← 注意，不是鲲鹏 |
| 镜像 | `Ubuntu 22.04 server 64bit`（由 `data.huaweicloud_images_image` 按机型过滤解析） |
| 系统盘 / 带宽 | SSD 40 GB / EIP 5 Mbps |
| 网络 | 新建 VPC `10.254.0.0/16`、子网 `10.254.1.0/24` |
| 安全组 | 入站 22（限来源 `x.x.x.x/32`）、入站 8000、出站全放；**未开 80/443** |
| 尝试次数 | **4 次**（`deployment-test-report-attempt-1..4.md`），全部停在同一处 |

### `terraform apply` 真实创建成功的资源（state 里有 ID）

| 资源类型 | 数量 | state 中的名字 |
|---|---:|---|
| `huaweicloud_vpc` | 1 | `auto-deploy-vpc-zhuangmingbai` |
| `huaweicloud_vpc_subnet` | 1 | `auto-deploy-subnet-zhuangmingbai` |
| `huaweicloud_networking_secgroup` | 1 | `auto-deploy-sg-zhuangmingbai` |
| `huaweicloud_networking_secgroup_rule` | 3 | ssh / app / egress_all |
| `huaweicloud_kps_keypair` | 1 | `auto-deploy-key-zhuangmingbai-*` |
| `huaweicloud_vpc_eip` | 1 | apply 日志显示 `Creation complete`（后随失败回滚/未入最终 state） |
| `huaweicloud_compute_instance` | **0** | **未创建** |

> 资源 ID、项目 ID、来源 IP、request_id 全部在本文里脱敏；
> 原始 `terraform.tfstate` 与 4 份报告只留在本机 `.auto-deploy/`，不进公开仓库。

### 失败点（原文错误码）

```
error creating server: POST https://ecs.cn-north-4.myhuaweicloud.com/v1.1/<project>/cloudservers
{"error":{"message":"Submit order failed, Insufficient account balance. Top up your account..",
          "code":"Ecs.7000"}}
```

**归因**：账号余额不足，属于商务/配额问题，不是代码、镜像、网络或凭据问题。
证据是同一份 plan 里前 7 类资源都 `Creation complete`，只有 ECS 下单被拒；
4 次重试都停在同一处，说明不是偶发抖动。

另一个独立错误不要混进来：预检阶段脚本侧的 IAM `GET /v3/projects` 返回
`APIGW.0301`（AK/SK 签名校验失败），脚本据此跳过项目查询、改用默认 region。
**Terraform provider 用的是另一路凭据，能正常创建资源**——
所以这两个 401 不能合并成一句「华为云 API 调不通」。
这与 `docs/华为云依赖与失效矩阵.md` 里「`not subscribed` ≠ 签名失败」是同一条纪律：
**诊断必须分开，否则会往错误方向修一天。**

---

## 三、这对项目主链路意味着什么

ECS 只是**公网入口**，不是判定依赖。`src/pipeline.py` 零第三方包、不联网，
`/health` 与全部 344 个单测在本机就能跑完。所以：

> ECS 没开起来 → 只影响「有没有一个能点开的在线 Demo」，
> **不影响任何一条审计结论**。GitHub Pages 承担复验页与回放页，本地
> `uvicorn main:app` 可完整演示。

这与失效矩阵第 8 行、以及「失败可以降级，但 `/health` 必须说实话」是同一套设计，
不是为失败找的借口。**IaC 的价值恰恰在于：失败发生在哪一步、影响了什么、
没影响什么，都能指到具体资源。**

---

## 四、硬件关联：五层，边界逐层收紧

| 层 | 状态 | 能说什么 | 不能说什么 |
|---|---|---|---|
| **ARM64 开发机** | ✅ 实测 | `macOS ... arm64`，344 测试 + 20 步自检全绿；判定引擎纯标准库，无 C 扩展轮子 | — |
| **Linux aarch64 原生（2026-11-09 新增）** | ✅ **实测** | GitHub 托管 `ubuntu-24.04-arm`（原生 aarch64 Linux，glibc 2.39）跑通判定链路，digest `52bf7b635204fd96` 与 macOS 一致 | ❌ 不是鲲鹏物理机，不是 openEuler 发行版，不涉及昇腾 |
| **跨环境确定性** | ✅ **可复算** | macOS arm64 / Linux aarch64 / x86_64 三边判定指纹逐字节一致；12 个环境变体 144 次字段比对 | ❌ 不等于「鲲鹏实例实测」，不是 reproducible build |
| **华为云 x86 ECS（`s6.large.2`）** | 🟡 IaC 跑到 apply、实例未创建 | 「用华为云 provider 真实申请过 ECS，失败码 `Ecs.7000`」 | ❌ 不能说「已在 ECS 上部署」 |
| **鲲鹏 ARM ECS** | ❌ **未实测** | 「同为 ARM64 指令集，理论上可直接运行；**同指令集上的判定确定性已被跨架构比对验证**」 | ❌ 不能说「已适配鲲鹏」「已在鲲鹏验证」 |
| **昇腾 NPU** | ❌ 未涉及 | 「模型调用全部走 API，不涉及本地推理芯片」 | ❌ 不能写任何 NPU 相关成果 |

### 4.1 Linux aarch64：为什么这一层比 macOS arm64 更接近鲲鹏

鲲鹏与 openEuler 的运行时是 **Linux + glibc + aarch64**；
macOS 的 arm64 只是指令集相同，用户态完全不同。
2026-10 起 GitHub 对公开仓免费提供原生 arm64 Linux runner
（`ubuntu-24.04-arm`，非 QEMU），于是可以在同一 OS 家族 + 同一指令集上跑：

| 项 | 值 | 来源 |
|---|---|---|
| arch / system | `aarch64` / `Linux` | job annotation（公开只读） |
| libc / python | `glibc 2.39` / `3.12.16` | 同上 |
| 判定指纹 | `52bf7b635204fd96` | 与 macOS 存档一致 |
| run | `42601765005`（job `aarch64-linux`） | `GET /actions/runs/42601765005` |

**它仍然不是鲲鹏**：不是鲲鹏物理机、不是 openEuler 发行版、不涉及昇腾。
C18 保持 ③ 档声明，不因这条升级。这条只是把「理论上能跑」变成
「Linux aarch64 上真的跑过，且结论与 macOS 逐字节一致」。

### 4.2 跨环境确定性证明了什么（2026-11-08）

`eval/determinism_matrix.py` 把「同为 ARM64 理论上能跑」这句推论，
收敛成一条能被第三方复算的证据：在 arm64 开发机采集判定指纹存档
（`data/determinism_arm64_v0.json`，`digest=52bf7b635204fd96`），
再让 GitHub Actions 的 **x86_64** runner 用 4 个 Python 版本、
**不装任何第三方包**的空 venv 当场算出同一个指纹。

方法来自 GitHub 上 `SOC_Replay` 的 reproducible-builds 与 MEP-51 的
cross-target byte-equal differential gate；后者逐条列出了历史上真正导致
跨架构分歧的原因（哈希种子、set 顺序、locale、时区、float 打印），
我们把这些原因逐项注入成 12 个变体。

**这条证据的边界必须一起读：**

1. 它证明的是**判定结论在不同指令集 / Python 版本 / locale / 可选依赖下的确定性**，
   不是构建产物字节相同，也不是「服务已在国产机器上跑起来」。
2. 存档来自 **macOS arm64 开发机**，不是华为云 aarch64 实例；
   比对用的是 **Linux x86_64 CI**。两边都不是鲲鹏 ECS。
3. 所以台账里 **C18「鲲鹏实例未实测、昇腾 NPU 未涉及」仍是 ③ 档声明，
   不因 C21 升级**。证据分档没有豁免权，包括对我们自己有利的时候。
4. 存档自带 `declares` 三条边界声明，少一条判据直接 FAIL——
   防止后来有人把这份报告当成适配认证去用。

### 鲲鹏预设（写清楚「只是预设」，不是成果）

IaC 已经把机型抽成变量，切换成本确实很低：

```hcl
# .auto-deploy/terraform/ecs.tf
flavor_name = var.instance_flavor_name
# data.tf 还会用同一个 flavor_id 去过滤兼容镜像
flavor_id   = var.instance_flavor_name
```

```json
// 现有（x86，实测申请过）
{ "instance_flavor_name": "s6.large.2",
  "instance_image_name": "Ubuntu 22.04 server 64bit" }

// 鲲鹏预设（**未实测**，镜像名与机型兼容性都要在控制台确认）
{ "instance_flavor_name": "<鲲鹏机型，控制台查询后填>",
  "instance_image_name": "<该机型可用的 aarch64 Ubuntu 22.04 镜像名>" }
```

**不填具体型号是有意的**：鲲鹏机型名与可用镜像随 region / AZ 变化，
没查过就写一个型号名，等于把猜测写成事实。
要补这条证据，需要一台余额充足的鲲鹏 ECS，跑：

```bash
python3 -m pip install -r requirements.txt
bash scripts/check_all.sh        # 期望 [ALL GREEN] 20 步
python3 eval/determinism_matrix.py --json   # 期望 cross_arch=true
uname -m                         # 期望 aarch64
```

把 `uname -m`、跨架构比对输出与 `[ALL GREEN]` 的完整日志贴进
`09_证据附录/运行存证/kunpeng_<date>.txt`，这条才从**推论**变成**证据**。
在那之前，材料里只写「ARM64 已验证、同指令集确定性已复算、鲲鹏未实测」。

---

## 五、怎么自己核对

原始证据在本机（不入库，避免把账号信息、密钥对、来源 IP 带进公开仓库）：

```bash
ls .auto-deploy/deployment-test-report-attempt-*.md       # 4 份，全部 failed
python3 - <<'PY'
import json
d=json.load(open('.auto-deploy/terraform/terraform.tfstate'))
for r in d['resources']:
    print(r['mode'], r['type'], r['name'], len(r.get('instances',[])))
PY
grep -n 'Ecs.7000' .auto-deploy/deployment-test-report.md
```

仓库里可核对的是这份**脱敏后的结论**，以及
`docs/华为云依赖与失效矩阵.md` 第 8 行的降级口径。

---

## 六、一句话口径

> 我们不是「接了华为云」就完事：Terraform 用 `huaweicloud` provider 1.90.0
> 在 `cn-north-4` 真实建出了 VPC、子网、安全组和密钥对，
> ECS 因 `Ecs.7000` 余额不足没建成；**失败点、影响面、恢复动作都能指到具体资源**。
> 硬件上只有 ARM64 开发机是实测；新增的跨环境确定性矩阵证明了
> 「arm64 与 x86_64 判定指纹一致」，但**鲲鹏实例仍未实测、昇腾仍未涉及**——
> 这两条边界不因新证据升级，比多写一句「全面适配国产软硬件」值钱。
