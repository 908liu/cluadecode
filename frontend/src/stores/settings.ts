import { defineStore } from 'pinia'
import { settingsApi } from '@/api/settings'
import { useAppStore } from '@/stores/app'
import type { ApiService, ServicePayload } from '@/types/api'

export const useSettingsStore = defineStore('settings', {
  state: () => ({
    services: [] as ApiService[],
    loading: false,
    error: ''
  }),
  getters: {
    chatServices: (state) => state.services.filter((item) => item.type === 'chat' && item.enabled),
    defaultChat: (state) => state.services.find((item) => item.type === 'chat' && item.is_default) || null,
    defaultEmbedding: (state) => state.services.find((item) => item.type === 'embedding' && item.is_default) || null
  },
  actions: {
    async fetchServices(): Promise<void> {
      this.loading = true
      this.error = ''
      try {
        const data = await settingsApi.services()
        this.services = data.items
      } catch (error) {
        this.error = error instanceof Error ? error.message : '加载服务失败'
        throw error
      } finally {
        this.loading = false
      }
    },
    async saveService(editing: ApiService | null, payload: ServicePayload): Promise<ApiService> {
      const saved = editing
        ? await settingsApi.updateService(editing.id, payload)
        : await settingsApi.createService(payload)
      await this.fetchServices()
      await useAppStore().fetchStatus(true)
      return saved
    },
    async deleteService(id: string): Promise<void> {
      await settingsApi.deleteService(id)
      await this.fetchServices()
      await useAppStore().fetchStatus(true)
    },
    async setDefault(id: string): Promise<void> {
      await settingsApi.setDefault(id)
      await this.fetchServices()
      await useAppStore().fetchStatus(true)
    },
    async setEnabled(id: string, enabled: boolean): Promise<void> {
      await settingsApi.setEnabled(id, enabled)
      await this.fetchServices()
      await useAppStore().fetchStatus(true)
    },
    async testService(id: string) {
      return settingsApi.testService(id)
    }
  }
})