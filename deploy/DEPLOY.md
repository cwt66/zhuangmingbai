# 部署清单 — zhuangmingbai（装明白 · 装修报价审计）

> 目标：从零 clone 到 `/health` 探活通过。

---

## 1. 前置条件

- 目标机器：Linux x86_64（Ubuntu/Debian 示例）
- 依赖：Python 3.13+、systemd、nginx
- 部署用户：`deploy`（需提前创建 `useradd -m deploy`）
- 代码目录：`/opt/zhuangmingbai`
- 服务端口：`8000`（通过 `PORT=8000` 注入）

---

## 2. 部署步骤

### 2.1 创建目录 & 上传文件

```bash
# 以 deploy 用户身份操作
sudo -u deploy mkdir -p /opt/zhuangmingbai
sudo chown deploy:deploy /opt/zhuangmingbai
```

将 `deploy/` 目录下的三个文件上传到目标机器：

| 本地文件 | 目标路径 | 属主/权限 |
|---|---|---|
| `deploy/zhuangmingbai.service` | `/etc/systemd/system/zhuangmingbai.service` | root:root / 644 |
| `deploy/nginx.conf` | `/etc/nginx/sites-available/zhuangmingbai` | root:root / 644 |
| `deploy/DEPLOY.md` | `/opt/zhuangmingbai/DEPLOY.md` | deploy:deploy / 644 |

### 2.2 拉取代码

```bash
sudo -u deploy -H bash -c '
cd /opt && \
git clone https://github.com/<your-org>/zhuangmingbai.git zhuangmingbai && \
cd zhuangmingbai && \
git checkout <target-tag-or-commit>
'
```

### 2.3 创建 venv & 安装依赖

```bash
sudo -u deploy -H bash -c '
cd /opt/zhuangmingbai && \
python3.13 -m venv venv && \
./venv/bin/pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
'
```

### 2.4 配置 nginx

```bash
# 启用站点
sudo ln -sf /etc/nginx/sites-available/zhuangmingbai /etc/nginx/sites-enabled/

# 移除默认站点（可选）
sudo rm -f /etc/nginx/sites-enabled/default

# 语法检查
sudo nginx -t

# 重载 nginx
sudo systemctl reload nginx
```

### 2.5 启动 systemd 服务

```bash
sudo systemctl daemon-reload
sudo systemctl enable zhuangmingbai
sudo systemctl start zhuangmingbai
```

### 2.6 探活验证

```bash
# 本地探活（直接访问后端）
curl -f http://127.0.0.1:8000/health

# 期望响应
# {"status":"ok","service":"zhuangmingbai-audit","version":"0.3.0"}

# 通过 nginx 探活
curl -f http://<server-ip>/health
```

---

## 3. 回滚

若探活失败，按以下步骤回滚到上一版本：

```bash
# 1. 进入代码目录
sudo -u deploy -H bash -c 'cd /opt/zhuangmingbai && git log --oneline -5'

# 2. 回滚到上一提交（将 HEAD~1 改为具体 commit 亦可）
sudo -u deploy -H bash -c 'cd /opt/zhuangmingbai && git reset --hard HEAD~1'

# 3. 重启服务
sudo systemctl restart zhuangmingbai

# 4. 再次探活
curl -f http://127.0.0.1:8000/health
```

> **注意**：若回滚涉及依赖变更，需重新执行 `2.3` 安装依赖步骤。

---

## 4. 常用运维命令

| 操作 | 命令 |
|---|---|
| 查看服务状态 | `systemctl status zhuangmingbai` |
| 重启服务 | `systemctl restart zhuangmingbai` |
| 停止服务 | `systemctl stop zhuangmingbai` |
| 查看日志 | `journalctl -u zhuangmingbai -f` |
| nginx 语法检查 | `nginx -t` |
| nginx 重载 | `systemctl reload nginx` |
| 手动运行探活 | `curl -f http://127.0.0.1:8000/health` |
