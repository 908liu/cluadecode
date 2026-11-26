import { request } from './http'
import type {
  EvalCaseHuman,
  EvalCaseModel,
  EvalCaseQuery,
  EvalDataset,
  EvalMetrics,
  EvalRun,
  Page
} from '@/types/api'

export interface CreateEvalRunPayload {
  dataset_id: string
  top_k: number
  threshold: number
  judge_service_id: string
  use_rerank?: boolean
  rerank_top_n?: number
  retrieval_strategy?: 'dense' | 'hybrid' | 'hybrid_rerank'
}

export const evaluationApi = {
  datasets(): Promise<{ items: EvalDataset[] }> {
    return request({ url: '/eval/datasets', method: 'get' })
  },
  createRun(payload: CreateEvalRunPayload): Promise<{ run_id: string; status: string }> {
    return request({ url: '/eval/runs', method: 'post', data: payload })
  },
  run(runId: string): Promise<EvalRun> {
    return request({ url: `/eval/runs/${runId}`, method: 'get' })
  },
  modelCases(runId: string, query: EvalCaseQuery): Promise<Page<EvalCaseModel>> {
    return request({ url: `/eval/runs/${runId}/cases`, method: 'get', params: { ...query, mode: 'model' } })
  },
  humanCases(runId: string, query: EvalCaseQuery): Promise<Page<EvalCaseHuman>> {
    return request({ url: `/eval/runs/${runId}/cases`, method: 'get', params: { ...query, mode: 'human' } })
  },
  saveHumanScore(runId: string, caseId: string, payload: { score: number | null; note: string }): Promise<{
    case_id: string
    human_score: number | null
    human_note: string
    human_scored_at: string | null
  }> {
    return request({
      url: `/eval/runs/${runId}/cases/${caseId}/human-score`,
      method: 'put',
      data: payload
    })
  },
  rescore(runId: string, judgeServiceId: string): Promise<{ run_id?: string; status: string }> {
    return request({ url: `/eval/runs/${runId}/rescore`, method: 'post', data: { judge_service_id: judgeServiceId } })
  },
  metrics(runId: string): Promise<EvalMetrics> {
    return request({ url: `/eval/runs/${runId}/metrics`, method: 'get' })
  },
  runs(params: { dataset_id?: string }): Promise<{ items: EvalRun[] }> {
    return request({ url: '/eval/runs', method: 'get', params })
  }
}