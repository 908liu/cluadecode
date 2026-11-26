import { defineStore } from 'pinia'
import { settingsApi } from '@/api/settings'
import type { SystemStatus } from '@/types/api'

export const useAppStore = defineStore('app', {
  state: () => ({
    status: null as SystemStatus | null,
    loading: false,
    error: ''
  }),
  getters: {
    chatConfigured: (state) => Boolean(state.status?.types.chat.configured),
    embeddingConfigured: (state) => Boolean(state.status?.types.embedding.configured),
    availableDocuments: (state) => state.status?.available_documents ?? 0,
    ready: (state) => Boolean(state.status?.ready)
  },
  actions: {
    async fetchStatus(force = false): Promise<SystemStatus | null> {
      if (this.status && !force) return this.status
      this.loading = true
      this.error = ''
      try {
        this.status = await settingsApi.status()
        return this.status
      } catch (error) {
        this.error = error instanceof Error ? error.message : '获取系统状态失败'
        return null
      } finally {
        this.loading = false
      }
    }
  }
})