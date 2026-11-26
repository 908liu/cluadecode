import { request } from './http'
import type {
  ApiService,
  ServicePayload,
  SystemStatus,
  TestConnectionResult
} from '@/types/api'

export const settingsApi = {
  status(): Promise<SystemStatus> {
    return request({ url: '/settings/status', method: 'get' })
  },
  services(): Promise<{ items: ApiService[] }> {
    return request({ url: '/settings/services', method: 'get' })
  },
  createService(payload: ServicePayload): Promise<ApiService> {
    return request({ url: '/settings/services', method: 'post', data: payload })
  },
  updateService(id: string, payload: ServicePayload): Promise<ApiService> {
    return request({ url: `/settings/services/${id}`, method: 'put', data: payload })
  },
  deleteService(id: string): Promise<{ id: string; deleted: boolean }> {
    return request({ url: `/settings/services/${id}`, method: 'delete' })
  },
  testService(id: string): Promise<TestConnectionResult> {
    return request({ url: `/settings/services/${id}/test`, method: 'post', timeout: 122000 })
  },
  setDefault(id: string): Promise<SystemStatus> {
    return request({ url: `/settings/services/${id}/default`, method: 'post' })
  },
  setEnabled(id: string, enabled: boolean): Promise<ApiService> {
    return request({ url: `/settings/services/${id}/enabled`, method: 'patch', data: { enabled } })
  },
  changes(): Promise<{ items: Array<Record<string, unknown>> }> {
    return request({ url: '/settings/changes', method: 'get' })
  }
}