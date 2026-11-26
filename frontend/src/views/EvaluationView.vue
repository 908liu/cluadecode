<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { apiErrorMessage } from '@/api/http'
import { useAppStore } from '@/stores/app'
import { useSettingsStore } from '@/stores/settings'
import { useEvaluationStore, type EvalMode, type HumanFilter } from '@/stores/evaluation'
import { usePolling } from '@/composables/usePolling'
import {
  formatMetric,
  formatMs,
  formatPercent,
  formatScore
} from '@/utils/format'
import type { EvalCaseHuman, EvalCaseModel } from '@/types/api'

const app = useAppStore()
const settings = useSettingsStore()
const evaluation = useEvaluationStore()

const topK = ref(4)
const threshold = ref(0.6)
const useRerank = ref(false)
const rerankTopN = ref(20)
const selectedJudge = ref('')
const rubricVisible = ref(false)
const starting = ref(false)
const saveTimers = new Map<string, number>()

const retrievalStrategy = ref<'dense' | 'hybrid' | 'hybrid_rerank'>('hybrid')

const modelMetricCards = computed(() => {
  const m = evaluation.metrics
  return [
    { label: '用例数', value: m ? String(m.total_cases) : '—' },
    { label: `Recall@${topK.value}`, value: formatMetric(m?.recall_at_k ?? null) },
    { label: 'MRR', value: formatMetric(m?.mrr ?? null) },
    { label: `NDCG@${m?.ndcg_k ?? topK.value}`, value: formatMetric(m?.ndcg_at_k ?? null) },
    { label: '引用有效率', value: formatPercent(m?.citation_valid_rate ?? null) },
    { label: '拒答正确率', value: formatPercent(m?.refusal_accuracy ?? null) },
    { label: '模型评分均分', value: formatMetric(m?.model_score_avg ?? null) },
    { label: '评分覆盖率', value: m ? `${m.model_scored}/${m.total_cases}` : '—' },
    { label: '平均耗时', value: formatMs(m?.avg_latency_ms ?? null) }
  ]
})

const humanMetricCards = computed(() => {
  const m = evaluation.metrics
  return [
    { label: '用例数', value: m ? String(m.total_cases) : '—' },
    { label: '已评 / 未评', value: m ? `${m.human_scored} / ${m.total_cases - m.human_scored}` : '—' },
    { label: '人工评分均分', value: formatMetric(m?.human_score_avg ?? null) },
    { label: '与模型一致率', value: formatPercent(m?.human_model_agreement ?? null) }
  ]
})

const metricCards = computed(() => (evaluation.mode === 'model' ? modelMetricCards.value : humanMetricCards.value))

const runProgress = computed(() => {
  const run = evaluation.run
  if (!run) return '—'
  return `${run.progress.done} / ${run.progress.total}`
})

const missingForRun = computed(() => {
  if (!app.status) return '正在获取系统状态…'
  if (!app.status.types.chat.configured) return '未配置默认对话服务'
  if (!app.status.types.embedding.configured) return '未配置 Embedding 服务'
  if (useRerank.value && !app.status.types.rerank.configured) return '未配置默认重排服务'
  if (!settings.chatServices.length) return '没有可用的评分服务'
  return ''
})

const canStart = computed(() => !evaluation.isRunning && !starting.value && !missingForRun.value && Boolean(selectedJudge.value))

const runStatusLabel = computed(() => {
  const status = evaluation.run?.status
  if (!status) return ''
  return {
    queued: '排队中',
    running: '运行中',
    succeeded: '已完成',
    failed: '失败'
  }[status]
})

const polling = usePolling(async () => {
  if (!evaluation.run?.id) return
  await evaluation.fetchRun()
  if (evaluation.run && evaluation.run.status !== 'queued' && evaluation.run.status !== 'running') {
    polling.stop()
    await loadFinishedRun()
  }
}, 1500)

async function loadFinishedRun(): Promise<void> {
  try {
    await evaluation.fetchMetrics()
    if (evaluation.mode === 'model') await evaluation.fetchModelCases()
    else await evaluation.fetchHumanCases()
  } catch (e) {
    ElMessage.error(apiErrorMessage(e, '加载评测结果失败'))
  }
}

function ensureJudge(): void {
  if (!selectedJudge.value) {
    selectedJudge.value = settings.defaultChat?.id || settings.chatServices[0]?.id || ''
  }
  if (selectedJudge.value && !settings.chatServices.some((item) => item.id === selectedJudge.value)) {
    selectedJudge.value = settings.chatServices[0]?.id || ''
  }
}

async function startRun(): Promise<void> {
  if (!canStart.value) return
  starting.value = true
  try {
    evaluation.metrics = null
    evaluation.modelCases = []
    evaluation.humanCases = []
    evaluation.modelTotal = 0
    evaluation.humanTotal = 0
    await evaluation.startRun({
      dataset_id: evaluation.datasetId,
      top_k: topK.value,
      threshold: threshold.value,
      judge_service_id: selectedJudge.value,
      use_rerank: useRerank.value,
      rerank_top_n: rerankTopN.value,
      retrieval_strategy: retrievalStrategy.value
    })
    polling.start()
  } catch (e) {
    ElMessage.error(apiErrorMessage(e, '开始评测失败'))
  } finally {
    starting.value = false
  }
}

async function rescore(): Promise<void> {
  if (!selectedJudge.value || !evaluation.run?.id) return
  try {
    await evaluation.rescore(selectedJudge.value)
    ElMessage.success('已开始重新模型评分')
    polling.start()
  } catch (e) {
    ElMessage.error(apiErrorMessage(e, '重新评分失败'))
  }
}

function loadCasesForMode(mode: EvalMode): void {
  if (!evaluation.run) return
  const task = mode === 'model' ? evaluation.fetchModelCases() : evaluation.fetchHumanCases()
  task.catch((e) => ElMessage.error(apiErrorMessage(e, '加载评测用例失败')))
}

function switchMode(mode: string | number | boolean | undefined): void {
  const next = String(mode) as EvalMode
  evaluation.setMode(next)
  loadCasesForMode(next)
}

function selectRetrievalStrategy(value: string | number | boolean | undefined): void {
  const next = String(value) as 'dense' | 'hybrid' | 'hybrid_rerank'
  retrievalStrategy.value = next
  useRerank.value = next === 'hybrid_rerank'
}

function changeHumanFilter(value: string): void {
  evaluation.setHumanFilter(value as HumanFilter)
  loadCasesForMode('human')
}

function onModelPage(page: number): void {
  evaluation.modelPage = page
  void evaluation.fetchModelCases()
}

function onHumanPage(page: number): void {
  evaluation.humanPage = page
  void evaluation.fetchHumanCases()
}

function scheduleSave(row: EvalCaseHuman): void {
  if (!evaluation.run?.id) return
  const old = saveTimers.get(row.case_id)
  if (old != null) window.clearTimeout(old)
  const timer = window.setTimeout(async () => {
    try {
      await evaluation.saveHumanScore(row.case_id, row.human_score, row.human_note)
    } catch (e) {
      ElMessage.error(apiErrorMessage(e, '保存评分失败'))
    }
  }, 500)
  saveTimers.set(row.case_id, timer)
}

function onHumanScore(row: EvalCaseHuman, value: number | string | null | undefined): void {
  row.human_score = value === '' || value == null ? null : Number(value)
  scheduleSave(row)
}

function onHumanNoteBlur(row: EvalCaseHuman): void {
  scheduleSave(row)
}

async function refreshPanel(): Promise<void> {
  try {
    await Promise.all([app.fetchStatus(true), settings.fetchServices(), evaluation.fetchDatasets()])
    ensureJudge()
    if (!evaluation.run?.id) return
    if (evaluation.isRunning) polling.start()
    else await loadFinishedRun()
  } catch (e) {
    ElMessage.error(apiErrorMessage(e, '加载评测配置失败'))
  }
}

onMounted(() => {
  void refreshPanel()
})
</script>

<template>
  <div class="page">
    <div class="page-inner">
      <div class="page-head">
        <div>
          <div class="page-title">评测面板</div>
          <div class="page-sub">标准评测集 · 自动指标 + 模型评分 + 人工复核</div>
        </div>
        <el-button @click="rubricVisible = true">评分标准</el-button>
      </div>

      <div class="toolbar-row">
        <el-radio-group :model-value="evaluation.mode" size="small" @change="switchMode">
          <el-radio-button value="model">模型评分</el-radio-button>
          <el-radio-button value="human">人工评分</el-radio-button>
        </el-radio-group>

        <span class="hint-text">数据集</span>
        <el-select v-model="evaluation.datasetId" style="width: 220px" size="small">
          <el-option v-for="dataset in evaluation.datasets" :key="dataset.id" :label="dataset.name" :value="dataset.id" />
        </el-select>

        <template v-if="evaluation.mode === 'model'">
          <span class="hint-text">检索方案</span>
          <el-select :model-value="retrievalStrategy" size="small" style="width: 150px" @change="selectRetrievalStrategy">
            <el-option label="Dense 向量" value="dense" />
            <el-option label="Hybrid 混合" value="hybrid" />
            <el-option label="Hybrid + Rerank" value="hybrid_rerank" />
          </el-select>
          <span class="hint-text">Top-K</span>
          <el-input-number v-model="topK" :min="1" :max="20" size="small" controls-position="right" style="width: 90px" />
          <span class="hint-text">阈值</span>
          <el-input-number v-model="threshold" :min="0" :max="1" :step="0.05" :precision="2" size="small" controls-position="right" style="width: 100px" />
          <el-checkbox v-model="useRerank" size="small">启用重排</el-checkbox>
          <template v-if="useRerank">
            <span class="hint-text">重排候选</span>
            <el-input-number v-model="rerankTopN" :min="1" :max="100" size="small" controls-position="right" style="width: 90px" />
          </template>
          <span class="hint-text">评分模型</span>
          <el-select v-model="selectedJudge" size="small" style="width: 200px" placeholder="选择评分服务">
            <el-option v-for="service in settings.chatServices" :key="service.id" :label="`${service.name}（${service.model}）`" :value="service.id" />
          </el-select>
          <el-button type="primary" :loading="starting" :disabled="!canStart" @click="startRun">开始评测</el-button>
          <el-button :disabled="!evaluation.run || evaluation.isRunning || !selectedJudge" @click="rescore">重新模型评分</el-button>
        </template>

        <template v-else>
          <span class="hint-text">筛选</span>
          <el-select :model-value="evaluation.humanFilter" size="small" style="width: 130px" @change="changeHumanFilter">
            <el-option label="全部用例" value="all" />
            <el-option label="未评" value="unscored" />
            <el-option label="已评" value="scored" />
          </el-select>
        </template>

        <span class="hint-text push" v-if="evaluation.run">{{ runStatusLabel }} · {{ runProgress }}</span>
      </div>

      <div v-if="missingForRun" class="page-error">
        {{ missingForRun }}，请先在设置中完成配置。
        <el-button text type="primary" @click="$router.push('/settings/api')">去设置</el-button>
      </div>

      <div v-if="evaluation.run?.status === 'failed'" class="page-error">
        评测运行失败：{{ evaluation.run.error_message || evaluation.run.error_code || '未知错误' }}
      </div>

      <div v-if="!evaluation.run" class="empty-state eval-empty">
        <strong>还没有评测运行</strong>
        <span>选择数据集和评分模型后开始评测。标准集包含 8 条用例（6 条可回答 + 2 条不可回答）。</span>
        <el-button type="primary" :disabled="!canStart" @click="startRun">开始评测</el-button>
      </div>

      <template v-else>
        <div class="metric-grid">
          <div v-for="item in metricCards" :key="item.label" class="metric-card">
            <div class="metric-label">{{ item.label }}</div>
            <div class="metric-value">{{ item.value }}</div>
          </div>
        </div>

        <div class="hint-text eval-note">
          <template v-if="evaluation.mode === 'model'">
            模型评分由所选评分模型自动完成，适合批量回归对比；人工评分在“人工评分”页单独进行。
          </template>
          <template v-else>
            人工评分默认隐藏模型分，避免先入为主；评分和备注会自动保存。
          </template>
        </div>

        <el-table v-if="evaluation.mode === 'model'" v-loading="evaluation.loading" :data="evaluation.modelCases" row-key="case_id">
          <el-table-column prop="question" label="Query" min-width="150" />
          <el-table-column label="标准答案 / 出处" min-width="220">
            <template #default="{ row }">
              <div>{{ row.reference_answer }}</div>
              <div class="hint-text">{{ row.gold_source }}</div>
            </template>
          </el-table-column>
          <el-table-column prop="answer" label="模型回答" min-width="220" />
          <el-table-column label="召回" width="80">
            <template #default="{ row }">
              <el-tag :type="row.recall_hit ? 'success' : 'danger'" size="small" effect="plain">
                {{ row.recall_hit ? '命中' : '未命中' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="NDCG" width="80">
            <template #default="{ row }">{{ formatMetric(row.ndcg_score) }}</template>
          </el-table-column>
          <el-table-column label="模型评分" width="90">
            <template #default="{ row }">{{ formatScore(row.model_score) }}</template>
          </el-table-column>
          <el-table-column label="评分说明" min-width="190">
            <template #default="{ row }">
              <span class="hint-text">{{ row.error || row.model_reason || '—' }}</span>
            </template>
          </el-table-column>
          <template #empty>
            <div class="empty-state"><strong>暂无模型评分结果</strong><span>运行完成后会显示用例明细。</span></div>
          </template>
        </el-table>

        <el-table v-else v-loading="evaluation.loading" :data="evaluation.humanCases" row-key="case_id">
          <el-table-column prop="question" label="Query" min-width="150" />
          <el-table-column prop="reference_answer" label="标准答案" min-width="190" />
          <el-table-column prop="answer" label="模型回答" min-width="220" />
          <el-table-column label="人工评分" width="150">
            <template #default="{ row }">
              <el-select
                :model-value="row.human_score == null ? '' : row.human_score"
                size="small"
                style="width: 132px"
                placeholder="未评"
                @change="(value: number | string | null | undefined) => onHumanScore(row, value)"
              >
                <el-option label="未评" value="" />
                <el-option v-for="score in [1, 2, 3, 4, 5]" :key="score" :label="`${score} 分`" :value="score" />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column label="备注" min-width="180">
            <template #default="{ row }">
              <el-input
                v-model="row.human_note"
                size="small"
                maxlength="2000"
                placeholder="备注（可选）"
                @blur="onHumanNoteBlur(row)"
              />
            </template>
          </el-table-column>
          <el-table-column label="状态" width="80">
            <template #default="{ row }">
              <el-tag :type="row.human_score == null ? 'warning' : 'success'" size="small" effect="plain">
                {{ row.human_score == null ? '未评' : '已评' }}
              </el-tag>
            </template>
          </el-table-column>
          <template #empty>
            <div class="empty-state"><strong>暂无可评用例</strong><span>先运行模型评测，再切到人工评分。</span></div>
          </template>
        </el-table>

        <el-pagination
          v-if="evaluation.mode === 'model' && evaluation.modelTotal > evaluation.pageSize"
          :current-page="evaluation.modelPage"
          :page-size="evaluation.pageSize"
          :total="evaluation.modelTotal"
          layout="prev, pager, next"
          @current-change="onModelPage"
        />
        <el-pagination
          v-if="evaluation.mode === 'human' && evaluation.humanTotal > evaluation.pageSize"
          :current-page="evaluation.humanPage"
          :page-size="evaluation.pageSize"
          :total="evaluation.humanTotal"
          layout="prev, pager, next"
          @current-change="onHumanPage"
        />
      </template>
    </div>

    <el-dialog v-model="rubricVisible" title="标准评分口径（v1）" width="560px">
      <p class="hint-text">模型评分和人工评分使用同一套 5 分制口径；人工评分默认盲评，不显示模型分。</p>
      <div class="rubric-row"><b>5 分</b><span>完全正确：覆盖全部关键点，引用准确</span></div>
      <div class="rubric-row"><b>4 分</b><span>基本正确：覆盖主要点，引用基本准确</span></div>
      <div class="rubric-row"><b>3 分</b><span>部分正确：遗漏次要信息或引用不完整</span></div>
      <div class="rubric-row"><b>2 分</b><span>大部分错误：仅少量相关信息</span></div>
      <div class="rubric-row"><b>1 分</b><span>错误、无关或编造</span></div>
      <p class="hint-text rubric-foot">拒答题单独判定：应拒且正确拒答记 5 分；应拒未拒或不应拒却拒记 1 分。</p>
      <p class="hint-text">标准配置：标准评测集 v1（8 条，含 2 条不可回答）、Top-K 4、阈值 0.6。</p>
    </el-dialog>
  </div>
</template>

<style scoped>
.eval-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  border: 1px solid var(--kb-border);
  border-radius: var(--kb-radius-md);
  background: var(--kb-surface);
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin-bottom: 14px;
}

.metric-card {
  padding: 16px;
  border: 1px solid var(--kb-border);
  border-radius: var(--kb-radius-md);
  background: var(--kb-surface);
}

.metric-label {
  color: var(--kb-text-tertiary);
  font-size: 12px;
}

.metric-value {
  margin-top: 6px;
  color: var(--kb-text);
  font-size: 24px;
  font-weight: 600;
}

.eval-note {
  margin-bottom: 14px;
}

.rubric-row {
  display: flex;
  gap: 14px;
  padding: 8px 0;
  border-bottom: 1px dashed var(--kb-border);
  font-size: 13px;
}

.rubric-row b {
  min-width: 44px;
}

.rubric-foot {
  margin-top: 14px;
}

@media (max-width: 1100px) {
  .metric-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>