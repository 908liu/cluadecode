import type { AxiosProgressEvent } from 'axios'
import { request } from './http'
import type {
  Chunk,
  DocumentDetail,
  DocumentListParams,
  DocumentStatusItem,
  DocumentSummary,
  Page,
  PreflightFile,
  PreflightResponse,
  ReindexTask,
  UploadDocumentPayload
} from '@/types/api'

export const documentsApi = {
  list(params: DocumentListParams): Promise<Page<DocumentSummary>> {
    return request({ url: '/documents', method: 'get', params })
  },
  detail(id: string): Promise<DocumentDetail> {
    return request({ url: `/documents/${id}`, method: 'get' })
  },
  chunks(id: string, params: { page?: number; page_size?: number }): Promise<Page<Chunk>> {
    return request({ url: `/documents/${id}/chunks`, method: 'get', params })
  },
  preflight(files: PreflightFile[]): Promise<PreflightResponse> {
    return request({ url: '/documents/preflight', method: 'post', data: { files } })
  },
  upload(payload: UploadDocumentPayload, onProgress?: (event: AxiosProgressEvent) => void): Promise<DocumentSummary> {
    const form = new FormData()
    form.append('file', payload.file)
    if (payload.uploader_display) form.append('uploader_display', payload.uploader_display)
    form.append('conflict_action', payload.conflict_action)
    if (payload.existing_document_id) form.append('existing_document_id', payload.existing_document_id)
    return request({
      url: '/documents/upload',
      method: 'post',
      data: form,
      timeout: 180000,
      onUploadProgress: onProgress
    })
  },
  retry(id: string): Promise<DocumentSummary> {
    return request({ url: `/documents/${id}/retry`, method: 'post' })
  },
  reindex(id: string): Promise<DocumentSummary> {
    return request({ url: `/documents/${id}/reindex`, method: 'post' })
  },
  reindexAll(documentIds?: string[]): Promise<{
    task_id: string
    accepted_document_ids: string[]
    rejected: Array<{ document_id: string; reason: string }>
  }> {
    return request({
      url: '/documents/reindex-all',
      method: 'post',
      data: documentIds ? { document_ids: documentIds } : {}
    })
  },
  reindexStatus(taskId: string): Promise<ReindexTask> {
    return request({ url: '/documents/reindex/status', method: 'get', params: { task_id: taskId } })
  },
  remove(id: string): Promise<Pick<DocumentSummary, 'id' | 'status' | 'deleted_at' | 'purge_at'>> {
    return request({ url: `/documents/${id}`, method: 'delete' })
  },
  restore(id: string): Promise<DocumentSummary> {
    return request({ url: `/documents/${id}/restore`, method: 'post' })
  },
  purge(id: string): Promise<{ id: string }> {
    return request({ url: `/documents/${id}/purge`, method: 'delete' })
  },
  statuses(ids: string[]): Promise<{ items: DocumentStatusItem[] }> {
    return request({
      url: '/documents/status',
      method: 'get',
      params: { ids: ids.join(',') }
    })
  }
}