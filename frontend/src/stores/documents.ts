import { defineStore } from 'pinia'
import { documentsApi } from '@/api/documents'
import type { DocumentListParams, DocumentStatusItem, DocumentSummary } from '@/types/api'

const defaultQuery: DocumentListParams = {
  page: 1,
  page_size: 20,
  q: '',
  status: 'all',
  format: 'all',
  sort: '-updated_at'
}

export const useDocumentsStore = defineStore('documents', {
  state: () => ({
    items: [] as DocumentSummary[],
    total: 0,
    query: { ...defaultQuery } as DocumentListParams,
    loading: false,
    error: ''
  }),
  getters: {
    processingIds: (state) =>
      state.items
        .filter((item) => ['queued', 'parsing', 'chunking', 'indexing'].includes(item.status))
        .map((item) => item.id)
  },
  actions: {
    setQuery(patch: Partial<DocumentListParams>): void {
      this.query = { ...this.query, ...patch }
    },
    resetQuery(): void {
      this.query = { ...defaultQuery }
    },
    async fetchList(): Promise<void> {
      this.loading = true
      this.error = ''
      try {
        const data = await documentsApi.list(this.query)
        this.items = data.items
        this.total = data.total
      } catch (error) {
        this.error = error instanceof Error ? error.message : '加载文档失败'
        throw error
      } finally {
        this.loading = false
      }
    },
    mergeStatuses(statuses: DocumentStatusItem[]): void {
      const map = new Map(statuses.map((item) => [item.id, item]))
      this.items = this.items.map((item) => {
        const next = map.get(item.id)
        if (!next) return item
        return {
          ...item,
          status: next.status === 'missing' ? item.status : next.status,
          chunks: next.chunks,
          progress: next.progress,
          queue_position: next.queue_position,
          error: next.error,
          error_code: next.error_code,
          updated_at: next.updated_at
        }
      })
    },
    removeItem(id: string): void {
      this.items = this.items.filter((item) => item.id !== id)
      this.total = Math.max(0, this.total - 1)
    },
    updateItem(next: DocumentSummary): void {
      this.items = this.items.map((item) => (item.id === next.id ? next : item))
    }
  }
})