import type { DocumentStatus, RefusalReason } from '@/types/api'

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`
  return `${bytes} B`
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
}

export function formatMs(value: number | null | undefined): string {
  if (value == null) return '—'
  if (value >= 1000) return `${(value / 1000).toFixed(1)} s`
  return `${Math.round(value)} ms`
}

export function formatPercent(value: number | null | undefined): string {
  if (value == null) return '—'
  return `${(value * 100).toFixed(1)}%`
}

export function formatScore(value: number | null | undefined): string {
  return value == null ? '—' : `${value} / 5`
}

export function formatMetric(value: number | null | undefined, digits = 2): string {
  return value == null ? '—' : value.toFixed(digits)
}

export interface DocumentStatusMeta {
  label: string
  tag: 'success' | 'warning' | 'danger' | 'info'
}

export const documentStatusMeta: Record<DocumentStatus, DocumentStatusMeta> = {
  queued: { label: '排队中', tag: 'warning' },
  parsing: { label: '解析中', tag: 'warning' },
  chunking: { label: '切分中', tag: 'warning' },
  indexing: { label: '索引中', tag: 'warning' },
  available: { label: '可用', tag: 'success' },
  failed: { label: '失败', tag: 'danger' },
  deleted: { label: '已删除', tag: 'info' }
}

export const documentErrorLabels: Record<string, string> = {
  unsupported_format: '文件格式不支持',
  too_large: '文件超过大小限制',
  empty_file: '文件为空',
  parse_failed: '文档解析失败',
  empty_text: '文档没有可提取的文字（扫描件 / 图片）',
  chunk_failed: '文档切分失败',
  embedding_failed: '向量生成失败，请检查 Embedding 服务',
  index_failed: '索引写入失败',
  cancelled: '任务已取消',
  internal: '服务内部错误'
}

export const refusalLabels: Record<RefusalReason, string> = {
  empty_kb: '知识库还没有可用文档',
  empty_retrieval: '没有检索到相关内容',
  insufficient_evidence: '找到的内容不足以回答',
  invalid_citation: '引用校验未通过，暂不展示答案'
}

export function documentErrorText(errorCode: string | null, fallback: string | null): string {
  if (errorCode && documentErrorLabels[errorCode]) return documentErrorLabels[errorCode]
  return fallback || '处理失败'
}