import { defineStore } from 'pinia'
import { evaluationApi, type CreateEvalRunPayload } from '@/api/evaluation'
import type {
  EvalCaseHuman,
  EvalCaseModel,
  EvalDataset,
  EvalMetrics,
  EvalRun
} from '@/types/api'

export type EvalMode = 'model' | 'human'
export type HumanFilter = 'all' | 'scored' | 'unscored'

export const useEvaluationStore = defineStore('evaluation', {
  state: () => ({
    datasets: [] as EvalDataset[],
    datasetId: 'std',
    mode: 'model' as EvalMode,
    run: null as EvalRun | null,
    metrics: null as EvalMetrics | null,
    modelCases: [] as EvalCaseModel[],
    humanCases: [] as EvalCaseHuman[],
    modelTotal: 0,
    humanTotal: 0,
    modelPage: 1,
    humanPage: 1,
    pageSize: 20,
    humanFilter: 'all' as HumanFilter,
    loading: false,
    error: ''
  }),
  getters: {
    totalCases: (state) => state.run?.progress.total ?? 0,
    doneCases: (state) => state.run?.progress.done ?? 0,
    isRunning: (state) => state.run?.status === 'queued' || state.run?.status === 'running'
  },
  actions: {
    async fetchDatasets(): Promise<void> {
      const data = await evaluationApi.datasets()
      this.datasets = data.items
      if (!this.datasets.some((item) => item.id === this.datasetId) && this.datasets[0]) {
        this.datasetId = this.datasets[0].id
      }
    },
    setMode(mode: EvalMode): void {
      this.mode = mode
    },
    setHumanFilter(filter: HumanFilter): void {
      this.humanFilter = filter
      this.humanPage = 1
    },
    async startRun(payload: CreateEvalRunPayload): Promise<string> {
      const data = await evaluationApi.createRun(payload)
      this.run = await evaluationApi.run(data.run_id)
      return data.run_id
    },
    async fetchRun(): Promise<void> {
      if (!this.run?.id) return
      this.run = await evaluationApi.run(this.run.id)
    },
    async fetchMetrics(): Promise<void> {
      if (!this.run?.id) return
      this.metrics = await evaluationApi.metrics(this.run.id)
    },
    async fetchModelCases(): Promise<void> {
      if (!this.run?.id) return
      const data = await evaluationApi.modelCases(this.run.id, {
        mode: 'model',
        page: this.modelPage,
        page_size: this.pageSize
      })
      this.modelCases = data.items
      this.modelTotal = data.total
    },
    async fetchHumanCases(): Promise<void> {
      if (!this.run?.id) return
      const data = await evaluationApi.humanCases(this.run.id, {
        mode: 'human',
        filter: this.humanFilter,
        page: this.humanPage,
        page_size: this.pageSize
      })
      this.humanCases = data.items
      this.humanTotal = data.total
    },
    async refreshRunData(): Promise<void> {
      await Promise.all([this.fetchRun(), this.fetchMetrics(), this.mode === 'model' ? this.fetchModelCases() : this.fetchHumanCases()])
    },
    async saveHumanScore(caseId: string, score: number | null, note: string): Promise<void> {
      if (!this.run?.id) return
      const saved = await evaluationApi.saveHumanScore(this.run.id, caseId, { score, note })
      const target = this.humanCases.find((item) => item.case_id === caseId)
      if (target) {
        target.human_score = saved.human_score
        target.human_note = saved.human_note
        target.human_scored_at = saved.human_scored_at
      }
      await this.fetchMetrics()
    },
    async rescore(judgeServiceId: string): Promise<void> {
      if (!this.run?.id) return
      await evaluationApi.rescore(this.run.id, judgeServiceId)
      await this.fetchRun()
    }
  }
})