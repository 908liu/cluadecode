import axios, { AxiosError, type AxiosRequestConfig, type AxiosResponse } from 'axios'
import type { ApiEnvelope } from '@/types/api'

export const TOKEN_KEY = 'kb_token'
export const USER_KEY = 'kb_user'

export class ApiError extends Error {
  readonly code: number
  readonly status: number
  readonly detail: unknown
  readonly retryAfter: number | null

  constructor(message: string, code = 0, status = 0, detail: unknown = null) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
    this.detail = detail
    const d = detail as { retry_after_seconds?: number } | null
    this.retryAfter = typeof d?.retry_after_seconds === 'number' ? d.retry_after_seconds : null
  }
}

export function isCanceled(error: unknown): boolean {
  return axios.isCancel(error)
}

export function apiErrorMessage(error: unknown, fallback = '请求失败，请稍后重试'): string {
  if (error instanceof ApiError) return error.message || fallback
  if (error instanceof Error) return error.message || fallback
  return fallback
}

export function apiErrorCode(error: unknown): number {
  return error instanceof ApiError ? error.code : 0
}

const baseURL = (import.meta.env.VITE_API_BASE || '/api/v1').replace(/\/$/, '')

const http = axios.create({
  baseURL,
  timeout: 15000
})

http.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY)
  if (token) {
    config.headers = config.headers || {}
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

http.interceptors.response.use(
  ((response: AxiosResponse) => {
    if (response.config.responseType === 'blob') return response
    const body = response.data as ApiEnvelope<unknown> | unknown
    if (body && typeof body === 'object' && 'code' in body) {
      const envelope = body as ApiEnvelope<unknown>
      if (envelope.code === 0) return envelope.data
      throw new ApiError(envelope.message || '请求失败', envelope.code, response.status, null)
    }
    return body
  }) as any,
  (error: AxiosError<{ code?: number; message?: string; detail?: unknown }>) => {
    if (axios.isCancel(error)) {
      return Promise.reject(new ApiError('已取消', -1, 0, null))
    }
    if (error.response) {
      const status = error.response.status
      const body = error.response.data as { code?: number; message?: string; detail?: unknown } | undefined
      if (status === 401) {
        localStorage.removeItem(TOKEN_KEY)
        localStorage.removeItem(USER_KEY)
        window.dispatchEvent(new CustomEvent('kb:unauthorized'))
      }
      const fallback =
        status === 401
          ? '登录已失效，请重新登录'
          : status === 403
            ? '没有权限执行该操作'
            : status === 404
              ? '资源不存在'
              : status === 429
                ? '请求过于频繁，请稍后重试'
                : status >= 500
                  ? '服务暂时不可用，请稍后重试'
                  : error.message || `请求失败（HTTP ${status}）`
      return Promise.reject(
        new ApiError(body?.message || fallback, body?.code ?? status, status, body?.detail ?? null)
      )
    }
    if (error.code === 'ECONNABORTED') {
      return Promise.reject(new ApiError('请求超时，请稍后重试', 50401, 0, null))
    }
    return Promise.reject(new ApiError('网络连接失败，请检查服务是否启动', 0, 0, null))
  }
)

export function request<T>(config: AxiosRequestConfig): Promise<T> {
  return http.request(config) as unknown as Promise<T>
}

export function buildApiPath(path: string): string {
  if (!path) return '/'
  const normalizedBase = baseURL.replace(/^\/api\/v1$/, '')
  if (path.startsWith(baseURL)) return path.slice(baseURL.length) || '/'
  if (path.startsWith('/api/v1')) return path.slice('/api/v1'.length) || '/'
  if (normalizedBase && path.startsWith(normalizedBase)) return path.slice(normalizedBase.length) || '/'
  return path.startsWith('/') ? path : `/${path}`
}

export async function downloadFile(path: string): Promise<{ blob: Blob; filename: string | null }> {
  const response = (await http.get<Blob>(buildApiPath(path), {
    responseType: 'blob',
    timeout: 120000
  })) as unknown as AxiosResponse<Blob>

  let filename: string | null = null
  const disposition = String(response.headers?.['content-disposition'] || '')
  if (disposition) {
    const parts = disposition.split(';')
    for (const part of parts) {
      const item = part.trim()
      const lower = item.toLowerCase()
      if (lower.startsWith('filename*=')) {
        const raw = item.slice('filename*='.length)
        const idx = raw.indexOf("''")
        filename = decodeURIComponent(idx >= 0 ? raw.slice(idx + 2) : raw)
        break
      }
      if (lower.startsWith('filename=')) {
        filename = item.slice('filename='.length).replace(/^"|"$/g, '')
      }
    }
  }
  return { blob: response.data, filename }
}

export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

export default http