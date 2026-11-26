<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'
import { apiErrorMessage } from '@/api/http'
import { searchApi } from '@/api/search'
import { useAppStore } from '@/stores/app'
import { useSettingsStore } from '@/stores/settings'
import type {
  ApiService,
  EnvServiceStatus,
  RerankProtocol,
  SearchSettings,
  ServiceType
} from '@/types/api'

const app = useAppStore()
const settings = useSettingsStore()

const dialogVisible = ref(false)
const editing = ref<ApiService | null>(null)
const saving = ref(false)
const savingSearch = ref(false)
const formRef = ref<FormInstance>()
const searchSettings = reactive<SearchSettings>({
  top_k: 4,
  rrf_k: 60,
  use_rerank: false,
  rerank_top_n: 20
})

const form = reactive({
  name: '',
  type: 'chat' as ServiceType,
  base_url: '',
  api_key: '',
  model: '',
  timeout_seconds: 20,
  dimensions: null as number | null,
  protocol: 'cohere' as RerankProtocol
})

const typeLabels: Record<ServiceType, string> = {
  chat: '对话',
  embedding: 'Embedding',
  rerank: '重排'
}

const typeOrder: ServiceType[] = ['chat', 'embedding', 'rerank']

function typeLabel(type: string): string {
  return typeLabels[type as ServiceType] || type
}

const protocolLabels: Record<RerankProtocol, string> = {
  cohere: 'Cohere/Jina 兼容',
  dashscope: 'DashScope 原生'
}

function protocolLabel(protocol: RerankProtocol | undefined): string {
  return protocolLabels[protocol || 'cohere']
}

const envServices = computed(() => {
  const status = app.status
  if (!status) return [] as Array<{ type: ServiceType; env: EnvServiceStatus; overridden: boolean }>
  return typeOrder
    .map((type) => ({
      type,
      env: status.types[type].env,
      overridden: status.types[type].source === 'default'
    }))
    .filter((item) => item.env.configured)
})

const keyValidator = (_rule: unknown, value: string, callback: (error?: string | Error) => void): void => {
  if (!editing.value && !value.trim()) callback(new Error('新建服务必须填写 API Key'))
  else callback()
}

const dimensionValidator = (_rule: unknown, value: number | null, callback: (error?: string | Error) => void): void => {
  if (form.type === 'embedding' && (!value || value < 1)) callback(new Error('Embedding 服务必须填写向量维度'))
  else callback()
}

const rules: FormRules = {
  name: [
    { required: true, message: '请填写服务名称', trigger: 'blur' },
    { max: 64, message: '服务名称不能超过 64 个字符', trigger: 'blur' }
  ],
  base_url: [
    { required: true, message: '请填写 Base URL', trigger: 'blur' },
    { pattern: /^https?:\/\/[^\s#]+$/, message: 'Base URL 必须是 http / https 地址', trigger: 'blur' }
  ],
  model: [{ required: true, message: '请填写模型名', trigger: 'blur' }],
  api_key: [{ validator: keyValidator, trigger: 'blur' }],
  dimensions: [{ validator: dimensionValidator, trigger: 'change' }]
}

function openCreate(): void {
  editing.value = null
  resetForm()
  dialogVisible.value = true
}

function openEdit(service: ApiService): void {
  editing.value = service
  form.name = service.name
  form.type = service.type
  form.base_url = service.base_url
  form.api_key = ''
  form.model = service.model
  form.timeout_seconds = service.timeout_seconds
  form.dimensions = service.dimensions
  form.protocol = service.protocol || 'cohere'
  dialogVisible.value = true
}

function resetForm(): void {
  form.name = ''
  form.type = 'chat'
  form.base_url = ''
  form.api_key = ''
  form.model = ''
  form.timeout_seconds = 20
  form.dimensions = null
  form.protocol = 'cohere'
  formRef.value?.clearValidate()
}

function onTypeChange(): void {
  if (form.type !== 'embedding') form.dimensions = null
  if (form.type !== 'rerank') form.protocol = 'cohere'
}

async function save(): Promise<void> {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  saving.value = true
  try {
    await settings.saveService(editing.value, {
      name: form.name.trim(),
      type: form.type,
      base_url: form.base_url.trim(),
      api_key: form.api_key.trim() || undefined,
      model: form.model.trim(),
      timeout_seconds: form.timeout_seconds,
      dimensions: form.type === 'embedding' ? form.dimensions : null,
      protocol: form.type === 'rerank' ? form.protocol : null
    })
    ElMessage.success(editing.value ? '服务已更新' : '服务已新增')
    if (editing.value?.type === 'embedding') {
      ElMessage.info('Embedding 配置已变更，如有现有索引请重建')
    }
    dialogVisible.value = false
  } catch (e) {
    ElMessage.error(apiErrorMessage(e, '保存服务失败'))
  } finally {
    saving.value = false
  }
}

async function test(service: ApiService): Promise<void> {
  try {
    const result = await settings.testService(service.id)
    if (result.success) {
      ElMessage.success(`连接成功 · ${service.model} · ${result.latency_ms} ms`)
    } else {
      ElMessage({
        type: 'error',
        message: `连接失败（${result.error_type || 'unknown'}）：${result.message}`,
        duration: 5000
      })
    }
  } catch (e) {
    ElMessage.error(apiErrorMessage(e, '测试连接失败'))
  }
}

async function setDefault(service: ApiService): Promise<void> {
  try {
    await settings.setDefault(service.id)
    ElMessage.success('已设为默认服务')
  } catch (e) {
    ElMessage.error(apiErrorMessage(e, '设置默认失败'))
  }
}

async function toggleEnabled(service: ApiService): Promise<void> {
  if (service.is_default && service.enabled) {
    ElMessage.warning('默认服务不能直接停用，请先设置同类型新的默认服务')
    return
  }
  try {
    await settings.setEnabled(service.id, !service.enabled)
    ElMessage.success(service.enabled ? '服务已停用' : '服务已启用')
  } catch (e) {
    ElMessage.error(apiErrorMessage(e, '更新服务状态失败'))
  }
}

async function remove(service: ApiService): Promise<void> {
  if (service.is_default) {
    ElMessage.warning('默认服务不能直接删除，请先设置同类型新的默认服务')
    return
  }
  try {
    await ElMessageBox.confirm(`确定删除服务「${service.name}」？删除后不可恢复。`, '删除服务', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning'
    })
    await settings.deleteService(service.id)
    ElMessage.success('服务已删除')
  } catch (e) {
    if (e === 'cancel' || e === 'close') return
    ElMessage.error(apiErrorMessage(e, '删除服务失败'))
  }
}

async function loadSearchSettings(): Promise<void> {
  const data = await searchApi.settings()
  Object.assign(searchSettings, data)
}

async function saveSearchSettings(): Promise<void> {
  savingSearch.value = true
  try {
    const data = await searchApi.saveSettings({ ...searchSettings })
    Object.assign(searchSettings, data)
    ElMessage.success('检索设置已保存')
  } catch (e) {
    ElMessage.error(apiErrorMessage(e, '保存检索设置失败'))
  } finally {
    savingSearch.value = false
  }
}

async function refresh(): Promise<void> {
  try {
    await Promise.all([settings.fetchServices(), app.fetchStatus(true), loadSearchSettings()])
  } catch (e) {
    ElMessage.error(apiErrorMessage(e, '加载配置失败'))
  }
}

onMounted(() => {
  void refresh()
})
</script>

<template>
  <div class="page">
    <div class="page-inner">
      <div class="page-head">
        <div>
          <div class="page-title">设置</div>
          <div class="page-sub">模型 API 配置、测试连接与默认服务</div>
        </div>
        <span class="pill">{{ app.status?.ready ? '服务已配置' : '配置不完整' }}</span>
      </div>

      <div v-if="app.status?.reindex_required" class="reindex-notice">
        <span>Embedding 服务已更换，需要重建索引。</span>
        <el-button text type="primary" @click="$router.push('/documents')">去知识库</el-button>
      </div>

      <div class="toolbar-row">
        <el-button type="primary" @click="openCreate">新增服务</el-button>
        <span class="hint-text">对话和 Embedding 默认服务配置后才能问答和上传；重排服务可选，用于提升检索排序。</span>
      </div>

      <el-table v-loading="settings.loading" :data="settings.services" row-key="id">
        <el-table-column prop="name" label="名称" min-width="140" />
        <el-table-column label="类型" width="110">
          <template #default="{ row }">{{ typeLabel(row.type) }}</template>
        </el-table-column>
        <el-table-column label="Base URL" min-width="220">
          <template #default="{ row }"><span class="mono">{{ row.base_url }}</span></template>
        </el-table-column>
        <el-table-column label="模型" min-width="180">
          <template #default="{ row }">
            {{ row.model }}<span v-if="row.dimensions" class="hint-text"> · {{ row.dimensions }} 维</span>
            <span v-if="row.type === 'rerank'" class="hint-text"> · {{ protocolLabel(row.protocol) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="Key" width="140">
          <template #default="{ row }"><span class="mono">{{ row.api_key_masked }}</span></template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'info'" size="small" effect="plain">
              {{ row.enabled ? '启用' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="默认" width="90">
          <template #default="{ row }">
            <span v-if="row.is_default" class="pill">默认</span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" min-width="270">
          <template #default="{ row }">
            <div class="table-actions">
              <el-button link size="small" @click="test(row)">测试</el-button>
              <el-button link size="small" :disabled="row.is_default || !row.enabled" @click="setDefault(row)">设默认</el-button>
              <el-button link size="small" :disabled="row.is_default" @click="toggleEnabled(row)">
                {{ row.enabled ? '停用' : '启用' }}
              </el-button>
              <el-button link size="small" @click="openEdit(row)">编辑</el-button>
              <el-button link size="small" type="danger" :disabled="row.is_default" @click="remove(row)">删除</el-button>
            </div>
          </template>
        </el-table-column>
        <template #empty>
          <div class="empty-state">
            <strong>还没有配置服务</strong>
            <span>新增对话和 Embedding 服务后，问答和上传才可用。</span>
          </div>
        </template>
      </el-table>

      <div v-if="envServices.length" class="card section-gap">
        <div class="env-head">
          <strong>环境变量服务</strong>
          <span class="hint-text">由服务端环境变量提供，只读；修改需重启服务。</span>
        </div>
        <div v-for="item in envServices" :key="item.type" class="env-row">
          <span class="tag">{{ typeLabel(item.type) }}</span>
          <span>{{ item.env.service?.name || '环境变量服务' }}</span>
          <span class="mono muted">{{ item.env.service?.model || '—' }}</span>
          <span v-if="item.overridden" class="hint-text">已被数据库默认覆盖</span>
          <span v-else class="pill">只读</span>
        </div>
      </div>

      <div class="card section-gap">
        <div class="env-head">
          <strong>检索设置</strong>
          <span class="hint-text">Top-K、RRF 和可选重排；重排失败会自动回退 RRF。</span>
        </div>
        <div class="search-grid">
          <div class="search-item">
            <span class="hint-text">Top-K</span>
            <el-input-number v-model="searchSettings.top_k" :min="1" :max="20" size="small" controls-position="right" />
          </div>
          <div class="search-item">
            <span class="hint-text">RRF K</span>
            <el-input-number v-model="searchSettings.rrf_k" :min="1" :max="1000" size="small" controls-position="right" />
          </div>
          <div class="search-item">
            <span class="hint-text">启用重排</span>
            <el-switch v-model="searchSettings.use_rerank" />
          </div>
          <div class="search-item">
            <span class="hint-text">重排候选数</span>
            <el-input-number
              v-model="searchSettings.rerank_top_n"
              :min="1"
              :max="100"
              size="small"
              controls-position="right"
              :disabled="!searchSettings.use_rerank"
            />
          </div>
        </div>
        <div v-if="searchSettings.use_rerank && !app.status?.types.rerank.configured" class="hint-text">
          未配置默认重排服务，开启后不会生效；请先在上方新增并设为默认。
        </div>
        <el-button type="primary" size="small" :loading="savingSearch" @click="saveSearchSettings">
          保存检索设置
        </el-button>
      </div>

      <el-dialog v-model="dialogVisible" :title="editing ? '编辑服务' : '新增服务'" width="600px" :close-on-click-modal="false">
        <el-form ref="formRef" :model="form" :rules="rules" label-position="top">
          <div class="field-grid">
            <el-form-item label="服务名称" prop="name">
              <el-input v-model="form.name" maxlength="64" placeholder="如：OpenAI 对话" />
            </el-form-item>
            <el-form-item label="类型" prop="type">
              <el-select v-model="form.type" :disabled="Boolean(editing)" style="width: 100%" @change="onTypeChange">
                <el-option label="对话" value="chat" />
                <el-option label="Embedding" value="embedding" />
                <el-option label="重排" value="rerank" />
              </el-select>
            </el-form-item>
          </div>
          <el-form-item label="Base URL" prop="base_url">
            <el-input v-model="form.base_url" placeholder="https://api.example.com/v1" />
          </el-form-item>
          <el-form-item label="API Key" prop="api_key">
            <el-input
              v-model="form.api_key"
              type="password"
              show-password
              :placeholder="editing ? '留空表示不修改' : 'sk-...'"
            />
          </el-form-item>
          <div class="field-grid">
            <el-form-item label="模型名" prop="model">
              <el-input v-model="form.model" placeholder="如：gpt-4o-mini" />
            </el-form-item>
            <el-form-item label="超时（秒）" prop="timeout_seconds">
              <el-input-number v-model="form.timeout_seconds" :min="1" :max="120" style="width: 100%" />
            </el-form-item>
          </div>
          <el-form-item v-if="form.type === 'embedding'" label="向量维度" prop="dimensions">
            <el-input-number v-model="form.dimensions" :min="1" :max="8192" style="width: 100%" placeholder="如 1024" />
          </el-form-item>
          <el-form-item v-if="form.type === 'rerank'" label="重排协议" prop="protocol">
            <el-select v-model="form.protocol" style="width: 100%">
              <el-option label="Cohere / Jina 兼容（/rerank）" value="cohere" />
              <el-option label="DashScope 原生（gte-rerank-v2）" value="dashscope" />
            </el-select>
            <div v-if="form.protocol === 'dashscope'" class="hint-text">
              Base URL 填 https://dashscope.aliyuncs.com，模型填 gte-rerank-v2；兼容模式不支持该模型。
            </div>
            <div v-else class="hint-text">
              Base URL 填服务根地址（如 https://api.example.com/v1），后端会调用 /rerank。
            </div>
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button :disabled="saving" @click="dialogVisible = false">取消</el-button>
          <el-button type="primary" :loading="saving" @click="save">保存</el-button>
        </template>
      </el-dialog>
    </div>
  </div>
</template>

<style scoped>
.reindex-notice {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
  padding: 12px 16px;
  border: 1px solid #f0d9c8;
  border-radius: var(--kb-radius-md);
  background: #fdf8f2;
  font-size: 13px;
}

.env-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.search-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 18px;
  margin-bottom: 12px;
}

.search-item {
  display: flex;
  align-items: center;
  gap: 8px;
}

.env-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 0;
  border-top: 1px dashed var(--kb-border);
  font-size: 13px;
}

.env-row:first-of-type {
  border-top: 0;
}
</style>