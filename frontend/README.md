# 文档问答知识库 · 前端 v1

Vue 3 + Vite + TypeScript + Element Plus + Pinia + Vue Router + Axios。

视觉基线：`../frontend-v1.html`（暖灰底 + 赤陶橙）。
接口契约：`../docs/frontend-dev.md` v1.3。

## 启动

```bash
npm install
cp .env.example .env
npm run dev
```

默认访问 `http://localhost:5173`，`/api` 代理到 `http://127.0.0.1:8000`。
可在 `.env` 中修改 `VITE_PROXY_TARGET` 和 `VITE_API_BASE`。

## 构建与类型检查

```bash
npm run build
npm run typecheck
```

## 页面

- `/register` 首次注册唯一账号
- `/login` 登录
- `/chat` 单轮问答、引用、拒答、原文下载
- `/documents` 上传、预检、冲突处理、列表、详情、回收站、重建索引
- `/evaluation` 标准评测集、模型评分、人工评分
- `/settings/api` API 配置（左下角设置入口进入）

## 约定

- 所有 JSON 接口按 `{ code, message, data }` 解包；`/health` 和下载除外。
- 登录态存 `localStorage` 的 `kb_token` / `kb_user`；401 清登录态并跳登录。
- 不内置 Mock 数据，所有页面直接请求接口。
- API Key 只在表单输入和提交请求中出现，不会进入 Pinia / localStorage。

## 当前范围

已覆盖主链路：注册 / 登录、对话、文档上传与列表、文档详情与回收站、评测模型/人工模式、API 配置。
P1（问答记录、检索调试、评测历史、评测集管理）保留了 `src/api` 类型和请求函数，页面未做。