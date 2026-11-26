import { request } from './http'
import type { SearchDebugResult, SearchSettings } from '@/types/api'

export const searchApi = {
  debug(payload: { query: string; top_k: number; use_rerank: boolean; rerank_top_n?: number }): Promise<SearchDebugResult> {
    return request({ url: '/search/debug', method: 'post', data: payload })
  },
  settings(): Promise<SearchSettings> {
    return request({ url: '/search/settings', method: 'get' })
  },
  saveSettings(payload: SearchSettings): Promise<SearchSettings> {
    return request({ url: '/search/settings', method: 'put', data: payload })
  }
}