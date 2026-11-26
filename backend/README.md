# 医知助手 · 医疗指南与流程知识库后端

FastAPI + SQLite（WAL）+ Chroma（本地持久化）单体后端，按 `docs/backend-dev.md` 和 `docs/requirements-v1.6.md` 实现。

当前架构总览见 [`docs/architecture.md`](../docs/architecture.md)。

## 环境

- Windows / Python 3.14（本机验证）
- 依赖由 uv 管理；**SQLAlchemy 固定 `<2.1`**（2.1 C 扩展在本机被 Application Control 阻止）
- 单进程、单 worker：SQLite FTS5 关键词索引、Chroma 本地向量库、进程内 FIFO 任务队列
- 问答支持 `POST /api/v1/qa/ask/stream`（SSE）；原 `/qa/ask` 非流式接口保留

## 安装

```bash
cd backend
uv venv .venv --python "D:/developTool/python.exe"
uv sync --extra dev
.venv/Scripts/python.exe -c "import sqlalchemy; print(sqlalchemy.__version__)"
```

如果 Anaconda Python 不可用，再尝试 uv-managed CPython 3.14：

```bash
uv venv .venv --python "C:/Users/19013/AppData/Roaming/uv/python/cpython-3.14.7-windows-x86_64-none/python.exe"
uv sync --extra dev
```

## 启动

```bash
cd backend
.venv/Scripts/python.exe -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --workers 1
```

- API：`http://127.0.0.1:8000/api/v1`
- 健康检查：`http://127.0.0.1:8000/health`
- OpenAPI：`http://127.0.0.1:8000/docs`
- 前端开发：Vite 5173 代理 `/api` 到 8000；`frontend/dist` 存在时后端也会托管 SPA。

## 首用

1. 打开前端登录页，进入注册页创建账号。注册模式由 `REGISTRATION_MODE`（`open` / `first-user` / `invite` / `closed`）控制，公网部署建议 `first-user` 或 `invite`。
2. 在设置页添加 chat / embedding 服务并分别设为默认。
3. 上传文档，等待状态变为可用。
4. 进入对话页提问；评测页可运行内置标准集（8 条）、公司制度集（12 条）和难评测集（hard，23 条，用于对比 rerank）。

## 阿里云百炼 / DashScope 配置

后端只要求服务提供 **OpenAI 兼容**接口。阿里云的兼容模式可以直接用：

| 类型 | Base URL | Model | Dimensions | Timeout |
|---|---|---|---|---|
| chat | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus`（或 `qwen-max`） | — | 30 |
| embedding | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `text-embedding-v3` | `1024` | 30 |

- 国际站把域名换成 `dashscope-intl.aliyuncs.com`。
- API Key 使用百炼控制台创建的 `sk-...` Key。
- 如果上传较大文档时 Embedding 报“单次请求文本数量超限”，把 `EMBEDDING_BATCH_SIZE` 改成 `10` 再启动后端（常见于 `text-embedding-v3`）。
- 维度必须和模型实际返回一致；`text-embedding-v3` 默认 1024，配置成其他维度时后端会因维度不一致报 `embedding_failed`。
- 设置页分别把 chat / embedding 设为默认后，`/settings/status` 的 `ready` 会变为 true。

## Rerank（可选）

Rerank 用户级默认关闭，不影响原有问答；用户在检索设置里开启后，检索会在 RRF 融合后对候选重排。`RERANK_ENABLED=0` 可全局禁用。

| 协议 | Base URL | Model | 说明 |
|---|---|---|---|
| `cohere` | 服务根地址，如 `https://api.example.com/v1` | 服务商模型名 | 后端调用 `POST {base}/rerank`，兼容 Cohere / Jina / qwen3-rerank 等 |
| `dashscope` | `https://dashscope.aliyuncs.com` | `gte-rerank-v2` | 走原生 `/api/v1/services/rerank/text-rerank/text-rerank`；**兼容模式不支持该模型** |

- 在设置页新增“重排”服务，选择协议并设为默认。
- 用户级开关：`PUT /api/v1/search/settings` 的 `use_rerank=true`；全局兜底：`RERANK_ENABLED=1`。
- 重排超时、限流、结构错误或未配置服务时自动回退 RRF，不会让问答失败。
- `RERANK_MIN_SCORE` 默认 `0`，表示不按重排分过滤，只按 `top_k` 截断；不同服务商分数尺度不同，开启前请先用评测校准。
- 调试：`POST /api/v1/search/debug` 返回 `keyword / vector / fused / reranked / final` 和 `rerank` 元信息。

```env
RERANK_ENABLED=1
RERANK_TOP_N=20
RERANK_MIN_SCORE=0.0
RERANK_TIMEOUT_CAP=5
RERANK_PROTOCOL=cohere
```

## 测试

```bash
cd backend
.venv/Scripts/python.exe -m pytest -q
```

测试使用临时 `DATA_DIR`；测试替身只放在 `tests/`，产品代码不包含 Mock。

## 数据与备份

| 项 | 默认 |
|---|---|
| 数据库 | `backend/data/app.db` |
| Chroma 向量库 | `backend/data/chroma/` |
| 上传文件 | `backend/data/uploads/{document_id}/` |
| JWT 密钥 | `data/.app_secret` |
| API Key 加密密钥 | `data/.encryption_key` |

备份必须同时包含 `app.db`、`chroma/`、`uploads/`、`.encryption_key`，且来自同一时间点。恢复后验证登录、服务 Key、文档下载和问答。

> Chroma 使用本地 PersistentClient，不是远程服务；删除 `data/chroma/` 后需要重建全部索引。

## 约束

- 不做多 worker、Redis、Celery、Alembic、远程向量库。
- Chroma 版本固定；依赖较大，安装后首次启动会初始化 `data/chroma/`。
- Rerank 只保留配置，不调用。
- 回收站/问答记录在启动时清理，不保证按天自动清理。
- 仅部署在可信内网；模型服务地址由管理员配置。