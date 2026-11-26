# 反向代理部署说明

> 公网部署请优先使用 Docker + Caddy 方案，见 [`docs/public-deployment.md`](../docs/public-deployment.md)。
> 本文件描述 Windows 原生启动 + Nginx 的替代方案。

## 本项目实际 API 前缀

前端使用 `/api/v1`；后端接口同样挂载在 `/api/v1`。

## 构建前端

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\build-frontend.ps1
```

构建产物位于 `frontend/dist/`。

## 启动后端

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\start-backend.ps1
```

后端只监听本机 `127.0.0.1:8000`。

## Nginx

- 使用 `deploy/nginx.conf` 作为站点配置参考。
- 修改 `server_name` 和 `root`。
- `/api/` 会代理到 `127.0.0.1:8000`。
- Nginx 顶部还需要定义：

```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    '' close;
}
```

如果 Nginx 与 FastAPI 共用同一服务器，建议将现有 `frontend/dist` 由 Nginx 提供；也可让 FastAPI 直接托管同一目录。

## 本机已验证

后端启动后可直接访问：

- 应用首页：`http://127.0.0.1:8000/`
- 健康检查：`http://127.0.0.1:8000/health`
- API 文档：`http://127.0.0.1:8000/docs`

当前已验证 `/health` 返回正常；FastAPI 会同时托管 `frontend/dist`，因此本机无需额外静态服务器。

若后续接入 Nginx，应继续使用同域 `/api/` 代理；同域访问不需要配置 `CORS_ORIGINS`。

## 生产环境注意事项

- `APP_SECRET` 必须由部署环境显式设置，不要使用空值。
- 数据目录、SQLite、Chroma、上传文件和加密密钥都需要持久化备份。
- 若使用域名和 HTTPS，需要使用完整的 `https://域名` 配置证书。
- 同域部署时无需配置 `CORS_ORIGINS`；若前端和后端使用不同域名，再按域名配置。
