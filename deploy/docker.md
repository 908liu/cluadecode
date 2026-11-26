# Docker 部署

公网部署的完整步骤见 [`docs/public-deployment.md`](../docs/public-deployment.md)。

## 快速开始

1. 复制环境变量：

```bash
cp .env.example .env
# 编辑 .env：APP_SECRET、DOMAIN、DATA_DIR_HOST
```

2. 构建并启动：

```bash
docker compose up -d --build
```

> 2C2G 服务器不要在本机构建镜像（容易 OOM）。先在本地 `docker buildx build --platform linux/amd64`，`docker save` 传到服务器 `docker load` 后，用 `docker compose up -d --no-build` 启动。详见 `docs/public-deployment.md` 第 3.1 节。

3. 访问：

- 公网域名：`https://DOMAIN`
- 本地测试（`DOMAIN=:80`）：`http://localhost`

应用容器不发布 8000 端口，外部流量统一经过 Caddy。

## 持久化

`DATA_DIR_HOST`（默认 `./data`）保存：

- SQLite 数据库
- Chroma 向量库
- 上传文件
- 加密密钥和 JWT 密钥

Linux 上必须先创建目录并交给 UID 10001：

```bash
sudo mkdir -p ./data
sudo chown -R 10001:10001 ./data
```

## 健康检查与日志

```bash
docker compose ps
docker compose logs --tail=100 app
docker compose exec app python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health').read().decode())"
curl -fsS https://DOMAIN/health
```

## 更新

```bash
docker compose up -d --build
```

数据保存在宿主机目录，重建容器不会丢失。更新前请先执行 `deploy/backup.sh`。

## 单容器架构

- FastAPI 后端：8000（仅内部网络）
- 前端静态文件：由 FastAPI SPA fallback 提供
- Caddy：80/443，负责 TLS、安全头和请求体限制