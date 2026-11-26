import { ApiError, request, TOKEN_KEY } from './http'
import type { AskResponse, Page } from '@/types/api'

const baseURL = (import.meta.env.VITE_API_BASE || '/api/v1').replace(/\/$/, '')

export interface AskStreamHandlers {
  onMeta?: (data: { request_id: string; model: string }) => void
  onDelta: (text: string) => void
  onFinal: (data: AskResponse) => void
}

function parseEventBlock(block: string): { event: string; data: unknown } | null {
  let event = ''
  const dataLines: string[] = []
  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
  }
  if (!event || !dataLines.length) return null
  try {
    return { event, data: JSON.parse(dataLines.join('\n')) }
  } catch {
    return null
  }
}

export interface QaRecord {
  id: string
  question: string
  answer: string
  refused: boolean
  refusal_reason: string | null
  model: string
  created_at: string
}

export const qaApi = {
  ask(question: string): Promise<AskResponse> {
    return request({
      url: '/qa/ask',
      method: 'post',
      data: { question },
      timeout: 120000
    })
  },
  async askStream(
    question: string,
    handlers: AskStreamHandlers,
    signal?: AbortSignal
  ): Promise<void> {
    const controller = new AbortController()
    const timeout = window.setTimeout(() => controller.abort(), 120000)
    const abort = (): void => controller.abort()
    if (signal) {
      if (signal.aborted) controller.abort()
      else signal.addEventListener('abort', abort, { once: true })
    }
    const token = localStorage.getItem(TOKEN_KEY)
    try {
      const response = await fetch(`${baseURL}/qa/ask/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'text/event-stream',
          ...(token ? { Authorization: `Bearer ${token}` } : {})
        },
        body: JSON.stringify({ question }),
        signal: controller.signal
      })
      if (!response.ok) {
        const body = await response.json().catch(() => null) as
          | { code?: number; message?: string; detail?: unknown }
          | null
        if (response.status === 401) {
          localStorage.removeItem(TOKEN_KEY)
          localStorage.removeItem('kb_user')
          window.dispatchEvent(new CustomEvent('kb:unauthorized'))
        }
        throw new ApiError(
          body?.message || `请求失败（HTTP ${response.status}）`,
          body?.code ?? response.status,
          response.status,
          body?.detail ?? null
        )
      }
      if (!response.body) throw new ApiError('浏览器不支持流式响应', 0, 0, null)

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      const dispatch = (block: string): void => {
        const parsed = parseEventBlock(block)
        if (!parsed) return
        if (parsed.event === 'meta') {
          handlers.onMeta?.(parsed.data as { request_id: string; model: string })
        } else if (parsed.event === 'delta') {
          const text = (parsed.data as { text?: string }).text
          if (text) handlers.onDelta(text)
        } else if (parsed.event === 'final') {
          handlers.onFinal(parsed.data as AskResponse)
        } else if (parsed.event === 'error') {
          const error = parsed.data as { code?: number; status?: number; message?: string; detail?: unknown }
          throw new ApiError(
            error.message || '流式回答失败',
            error.code ?? 50001,
            error.status ?? 0,
            error.detail ?? null
          )
        }
      }

      for (;;) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const blocks = buffer.split(/\r?\n\r?\n/)
        buffer = blocks.pop() || ''
        for (const block of blocks) dispatch(block)
      }
      buffer += decoder.decode()
      if (buffer.trim()) dispatch(buffer)
    } catch (error) {
      if (error instanceof ApiError) throw error
      if (controller.signal.aborted) throw new ApiError('请求超时，请稍后重试', 50401, 0, null)
      throw new ApiError('网络连接失败，请检查服务是否启动', 0, 0, null)
    } finally {
      window.clearTimeout(timeout)
      signal?.removeEventListener('abort', abort)
    }
  },
  records(params: { page?: number; page_size?: number }): Promise<Page<QaRecord>> {
    return request({ url: '/qa/records', method: 'get', params })
  },
  record(id: string): Promise<QaRecord> {
    return request({ url: `/qa/records/${id}`, method: 'get' })
  },
  removeRecord(id: string): Promise<void> {
    return request({ url: `/qa/records/${id}`, method: 'delete' })
  }
}