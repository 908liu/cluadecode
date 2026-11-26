# 公网部署指南（Docker + Caddy）

目标架构：

```
Internet ── 80/443 ──> Caddy（TLS、安全头、请求体限制、限流 IP 透传）
                          │ 仅 Docker 内部网络
                          └──> app:8000（FastAPI + 前端静态文件）
                                  │
                                  └── /opt/mediknow/data（SQLite/Chroma/上传/密钥）
```

应用容器不发布任何端口，公网只能经过 Caddy。容器以 UID 10001 非 root 运行，丢弃全部 capability，根文件系统只读，`/tmp` 使用 tmpfs。

## 1. 前置条件

- 一台公网 Linux 服务器（2 核 2G 可运行，但必须加 4G swap，并建议在本地构建镜像；2 核 4G 更稳妥）
- 一个域名，A 记录指向服务器公网 IP
- 云厂商安全组放行 TCP 22、80、443；**不要放行 8000**
- 服务器已安装 Docker 与 Compose 插件

```bash
# 以 root 或 sudo 执行
apt-get update
apt-get install -y ca-certificates curl sqlite3
curl -fsSL https://get.docker.com | sh
docker --version && docker compose version
```

如果服务器拉取 Docker Hub 较慢，先给 Docker 配置镜像加速，或在本机构建镜像后 `docker save`/`docker load` 传输。

### 2C2G 服务器必须加 swap

```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h && swapon --show
```

## 2. 上传代码

在本地项目根目录执行（不要带 `.env`、`backend/data`、`node_modules`、`.venv`）：

```bash
rsync -av \
  --exclude '.git' --exclude '.env' --exclude 'node_modules' \
  --exclude '.venv' --exclude '__pycache__' --exclude 'backend/data' \
  --exclude 'frontend/dist' --exclude 'data' --exclude '.tmp' \
  ./ user@SERVER_IP:/opt/mediknow/
```

服务器上准备数据目录，并交给容器内的 UID 10001：

```bash
sudo mkdir -p /opt/mediknow/data /opt/mediknow/backups
sudo chown -R 10001:10001 /opt/mediknow/data
sudo chmod 700 /opt/mediknow/data /opt/mediknow/backups
```

## 3. 配置 `.env`

```bash
cd /opt/mediknow
cp .env.example .env
chmod 600 .env
openssl rand -hex 32   # 把输出填到 APP_SECRET
```

`/opt/mediknow/.env` 至少包含：

```env
APP_SECRET=<openssl rand -hex 32 的输出>
DOMAIN=kb.example.com
REGISTRATION_MODE=first-user
BLOCK_PRIVATE_UPSTREAM=1
MODEL_HOST_ALLOWLIST=
DATA_DIR_HOST=/opt/mediknow/data
APP_MEM_LIMIT=1536m
```

说明：

| 变量 | 作用 |
|---|---|
| `APP_SECRET` | JWT 签名密钥，缺失时 compose 直接拒绝启动 |
| `DOMAIN` | 自动 HTTPS 的域名；本地纯 HTTP 测试用 `:80` |
| `REGISTRATION_MODE` | `open` / `first-user` / `invite` / `closed` |
| `REGISTRATION_INVITE_CODE` | `invite` 模式下的邀请码 |
| `BLOCK_PRIVATE_UPSTREAM` | 1 = 拒绝模型服务指向本机/内网/云元数据 |
| `MODEL_HOST_ALLOWLIST` | 可选域名白名单，逗号分隔；白名单内的主机被视为受信任（可明确放行自建内网模型服务） |
| `DATA_DIR_HOST` | 宿主机数据目录，必须属于 UID 10001 |
| `APP_MEM_LIMIT` | app 容器内存上限；2C2G 用 `1536m`，4G 可调到 `3g` |
| `MAX_NORMAL_USERS` | 普通账号总数上限，默认 10；管理员账号不计入 |
| `REGISTER_RATE_LIMIT` / `REGISTER_RATE_WINDOW` | 每 IP 注册请求限流；压测时可临时调高，默认 5 次 / 60 秒 |

## 3.1 2C2G 服务器：本地构建镜像（推荐）

2G 内存上同时跑前端构建、`uv sync` 和 Chroma 很容易 OOM。建议在本地构建好 `linux/amd64` 镜像再传上去。

本地（项目根目录，Git Bash）：

```bash
docker buildx build --platform linux/amd64 -t mediknow-rag:latest --load .
docker save mediknow-rag:latest | gzip -1 > mediknow-rag-image.tar.gz
scp mediknow-rag-image.tar.gz user@SERVER_IP:/tmp/
```

PowerShell 下管道会破坏二进制输出，请用 `cmd /c`：

```powershell
docker buildx build --platform linux/amd64 -t mediknow-rag:latest --load .
cmd /c "docker save mediknow-rag:latest | gzip -1 > mediknow-rag-image.tar.gz"
```

服务器上（先 `uname -m` 确认是 `x86_64`；ARM 服务器把平台改成 `linux/arm64`）：

```bash
sudo docker load -i /tmp/mediknow-rag-image.tar.gz
cd /opt/mediknow
sudo docker compose up -d --no-build
```

后续更新也用同样方式：本地构建 → 传镜像 → `docker compose up -d --no-build`。

## 4. 启动

```bash
cd /opt/mediknow
docker compose up -d --build
docker compose ps
docker compose logs -f --tail=100 app
```

首次启动会构建前端、安装 Python 依赖并初始化数据库，通常需要几分钟。健康后访问：

```text
https://kb.example.com
```

### 创建第一个账号

1. 打开 `/register` 注册首个账号；首个账号会获得管理员标记，管理员密码由注册时设置。
2. 确认注册已自动关闭：

```bash
curl -s https://kb.example.com/api/v1/auth/registration-status
# {"code":0,...,"data":{"registered":true,"mode":"first-user","registration_allowed":false,...}}
```

后续需要给同事开账号时，改成邀请制并重启：

```env
REGISTRATION_MODE=invite
REGISTRATION_INVITE_CODE=<随机邀请码>
```

```bash
docker compose up -d app
```

管理员标记用于识别首个账号；当前版本尚无管理员后台或账号管理功能。若要压测注册，临时设置 `REGISTRATION_MODE=open`、`MAX_NORMAL_USERS=10` 和 `REGISTER_RATE_LIMIT=20`，然后执行 `docker compose up -d app`。压测账号达到上限后会被拒绝；压测结束后建议改回 `invite` 或 `first-user`。

## 5. 数据迁移（可选）

如果要把本机 `backend/data` 的账号、文档和检索数据迁到服务器：

1. 先停止本地后端，确保 SQLite WAL 落盘。
2. 在本地打包：

```bash
tar -czf mediknow-data.tar.gz -C backend data
```

3. 上传并解压到服务器数据目录：

```bash
scp mediknow-data.tar.gz user@SERVER_IP:/tmp/
sudo tar -xzf /tmp/mediknow-data.tar.gz -C /opt/mediknow/ --strip-components=1
sudo chown -R 10001:10001 /opt/mediknow/data
```

注意：`.encryption_key` 必须一起迁移，否则已保存的模型 API Key 无法解密。

## 6. 备份

`deploy/backup.sh` 会备份 SQLite、Chroma、上传文件和两个密钥文件：

```bash
chmod +x /opt/mediknow/deploy/backup.sh
sudo /opt/mediknow/deploy/backup.sh
```

建议每天停机几秒做一致性备份：

```bash
sudo crontab -e
```

```cron
15 3 * * * STOP_APP=1 COMPOSE_DIR=/opt/mediknow DATA_DIR=/opt/mediknow/data BACKUP_DIR=/opt/mediknow/backups /opt/mediknow/deploy/backup.sh >> /var/log/mediknow-backup.log 2>&1
```

备份包含密钥，泄露等同泄露所有模型 API Key；请限制目录权限并考虑加密存储。

## 7. 更新与回滚

```bash
cd /opt/mediknow
docker tag mediknow-rag:latest mediknow-rag:rollback
# 2C2G：本地构建并传到服务器后加载镜像，再启动
docker load -i /tmp/mediknow-rag-image.tar.gz
docker compose up -d --no-build

# 4G 及以上也可以在服务器上直接重建
# docker compose up -d --build
```

更新前先备份数据。2C2G 不要在服务器上 `--build`。回滚：

```bash
docker compose down
docker tag mediknow-rag:rollback mediknow-rag:latest
docker compose up -d
```

## 8. 本机测试

Windows/macOS 安装并启动 Docker Desktop 后：

```bash
docker compose up -d --build
```

打开 `http://localhost`。仓库内的 `.env` 已按本地测试配置好（`DOMAIN=:80`、`REGISTRATION_MODE=first-user`）。如果 80 端口被占用，把 `.env` 的 `DOMAIN` 和 compose 的端口映射一起改掉。

本地测试时如果模型服务在本机（如 `http://127.0.0.1:9000`），需要临时设置 `BLOCK_PRIVATE_UPSTREAM=0`；公网部署必须保持 `1`。

## 9. 故障排查

| 现象 | 排查 |
|---|---|
| 502 / 无法访问 | `docker compose ps`、`docker compose logs app`；确认 app 健康 |
| 容器启动报权限错误 | `/opt/mediknow/data` 是否属于 UID 10001 |
| HTTPS 证书失败 | 域名 A 记录是否指向本机；安全组是否放行 80/443 |
| 上传报 413 | Caddyfile 的 `request_body max_size`、Nginx 的 `client_max_body_size` |
| 修改 Caddyfile 不生效 | Caddyfile 已关闭 admin API，执行 `docker compose restart caddy` |
| 模型服务报 `blocked` | 地址指向私网，或不在 `MODEL_HOST_ALLOWLIST` 中 |
| 注册提示已关闭 | `REGISTRATION_MODE=first-user` 时属正常行为 |
| 忘记密码 | `docker compose exec app .venv/bin/python -m tools.reset_password --username 用户名` |
| 容器反复重启 / exit 137 | 内存不足：确认 4G swap 已启用，`docker stats` 观察；避免一次上传超大文档 |
| 服务器上 build 卡死或 OOM | 2C2G 不要本地构建，按 3.1 在本地构建后传镜像 |

## 10. 上线前检查清单

- [ ] `.env` 权限 600，`APP_SECRET` 为随机值
- [ ] 2C2G 服务器已启用 4G swap，且镜像是在本地构建后传上去的
- [ ] `docker compose ps` 中 app 没有发布 8000 端口
- [ ] 云安全组和 `ufw` 只放行 22/80/443
- [ ] `BLOCK_PRIVATE_UPSTREAM=1`
- [ ] 首个账号已创建，注册状态为关闭或邀请制
- [ ] 备份任务已执行并验证可解压
- [ ] 已确认上传内容不包含不宜公网存储的患者隐私数据