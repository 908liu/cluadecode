<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { apiErrorMessage, downloadFile, saveBlob } from '@/api/http'
import { documentsApi } from '@/api/documents'
import { useAppStore } from '@/stores/app'
import { useAuthStore } from '@/stores/auth'
import { useDocumentsStore } from '@/stores/documents'
import { usePolling } from '@/composables/usePolling'
import {
  documentErrorText,
  documentStatusMeta,
  formatBytes,
  formatDate
} from '@/utils/format'
import {
  ALLOWED_EXTENSIONS,
  localFileError,
  makeClientId,
  sha256OfFile
} from '@/utils/file'
import type {
  Chunk,
  ConflictAction,
  DocumentDetail,
  DocumentFilterStatus,
  DocumentFormat,
  DocumentStatus,
  DocumentStatusItem,
  DocumentSummary,
  PreflightResult,
  ReindexTask
} from '@/types/api'

interface UploadItem {
  id: string
  file: File
  name: string
  size: number
  status: 'checking' | 'ready' | 'conflict' | 'uploading' | 'done' | 'failed'
  progress: number
  error: string
  result: PreflightResult | null
  action: ConflictAction
  existingId?: string
  sha256?: string
}

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const app = useAppStore()
const documentsStore = useDocumentsStore()

const search = ref('')
const searchTimer = ref<number | null>(null)
const uploadVisible = ref(false)
const uploaderDisplay = ref(auth.user?.display_name || '')
const queue = ref<UploadItem[]>([])
const fileInput = ref<HTMLInputElement | null>(null)
const uploading = ref(false)
const dragOver = ref(false)

const previewVisible = ref(false)
const previewLoading = ref(false)
const previewDoc = ref<DocumentDetail | null>(null)
const previewChunks = ref<Chunk[]>([])
const previewPage = ref(1)
const previewTotal = ref(0)
const previewPageSize = 20

const reindexTask = ref<ReindexTask | null>(null)

function statusMeta(status: DocumentStatus) {
  return documentStatusMeta[status] || documentStatusMeta.queued
}

const formatOptions: Array<'all' | DocumentFormat> = ['all', 'PDF', 'Word', 'Markdown']
const statusOptions: Array<{ value: DocumentFilterStatus; label: string }> = [
  { value: 'all', label: '全部状态' },
  { value: 'available', label: '可用' },
  { value: 'processing', label: '处理中' },
  { value: 'failed', label: '失败' },
  { value: 'trash', label: '回收站' }
]

const readyCount = computed(() => queue.value.filter((item) => item.status === 'ready').length)
const conflictCount = computed(() => queue.value.filter((item) => item.status === 'conflict').length)
const uploadingCount = computed(() => queue.value.filter((item) => item.status === 'uploading').length)
const canStartUpload = computed(() => readyCount.value > 0 && !uploading.value)

const polling = usePolling(async () => {
  const ids = documentsStore.processingIds
  if (!ids.length) {
    polling.stop()
    return
  }
  const items: DocumentStatusItem[] = []
  for (let start = 0; start < ids.length; start += 50) {
    const data = await documentsApi.statuses(ids.slice(start, start + 50))
    items.push(...data.items)
  }
  documentsStore.mergeStatuses(items)
  if (!documentsStore.processingIds.length) polling.stop()
}, 2000)

function parseStatus(value: unknown): DocumentFilterStatus {
  return statusOptions.some((item) => item.value === value) ? (value as DocumentFilterStatus) : 'all'
}

function parseFormat(value: unknown): 'all' | DocumentFormat {
  return formatOptions.includes(value as 'all' | DocumentFormat) ? (value as 'all' | DocumentFormat) : 'all'
}

function readQuery(): void {
  const page = Number(route.query.page) || 1
  const pageSize = Number(route.query.page_size) || 20
  documentsStore.setQuery({
    page,
    page_size: pageSize,
    q: typeof route.query.q === 'string' ? route.query.q : '',
    status: parseStatus(route.query.status),
    format: parseFormat(route.query.format),
    sort: typeof route.query.sort === 'string' ? route.query.sort : '-updated_at'
  })
  search.value = documentsStore.query.q || ''
}

function writeQuery(): void {
  const query = documentsStore.query
  void router.replace({
    query: {
      page: String(query.page || 1),
      page_size: String(query.page_size || 20),
      q: query.q || '',
      status: query.status || 'all',
      format: query.format || 'all',
      sort: query.sort || '-updated_at'
    }
  })
}

async function refreshList(): Promise<void> {
  try {
    await documentsStore.fetchList()
    if (documentsStore.processingIds.length) polling.start()
    else polling.stop()
  } catch (e) {
    ElMessage.error(apiErrorMessage(e, '加载文档失败'))
  }
}

function applyFilters(): void {
  documentsStore.setQuery({
    q: search.value.trim(),
    page: 1
  })
  writeQuery()
  void refreshList()
}

function onSearchInput(): void {
  if (searchTimer.value != null) window.clearTimeout(searchTimer.value)
  searchTimer.value = window.setTimeout(() => applyFilters(), 300)
}

function onPageChange(page: number): void {
  documentsStore.setQuery({ page })
  writeQuery()
  void refreshList()
}

/* ---------------- upload ---------------- */

function openUpload(): void {
  if (!app.embeddingConfigured) {
    ElMessage.warning('请先在设置中配置默认 Embedding 服务')
    return
  }
  uploaderDisplay.value = auth.user?.display_name || ''
  queue.value = []
  uploadVisible.value = true
}

function closeUpload(): void {
  if (uploading.value) return
  uploadVisible.value = false
  queue.value = []
}

function pickFiles(): void {
  fileInput.value?.click()
}

function onFileChange(event: Event): void {
  const target = event.target as HTMLInputElement
  const files = Array.from(target.files || [])
  target.value = ''
  void addFiles(files)
}

function onDrop(event: DragEvent): void {
  event.preventDefault()
  dragOver.value = false
  const files = Array.from(event.dataTransfer?.files || [])
  void addFiles(files)
}

async function addFiles(files: File[]): Promise<void> {
  const accepted: UploadItem[] = []
  for (const file of files) {
    if (queue.value.length + accepted.length >= 20) {
      ElMessage.warning('单次最多选择 20 个文件')
      break
    }
    const localError = localFileError(file)
    const item: UploadItem = {
      id: makeClientId(),
      file,
      name: file.name,
      size: file.size,
      status: localError ? 'failed' : 'checking',
      progress: 0,
      error: localError || '',
      result: null,
      action: 'none'
    }
    accepted.push(item)
  }
  if (!accepted.length) return
  queue.value.push(...accepted)

  // 必须从 queue.value 取代理对象再修改；直接改 accepted 里的原始对象不会触发视图更新
  const pendingIds = new Set(accepted.filter((item) => item.status === 'checking').map((item) => item.id))
  const pending = queue.value.filter((item) => pendingIds.has(item.id))
  for (const item of pending) {
    item.status = 'checking'
    const hash = await sha256OfFile(item.file)
    item.result = null
    item.sha256 = hash
  }

  for (let start = 0; start < pending.length; start += 20) {
    const batch = pending.slice(start, start + 20)
    try {
      const response = await documentsApi.preflight(
        batch.map((item) => ({
          client_id: item.id,
          name: item.name,
          size_bytes: item.size,
          sha256: item.sha256
        }))
      )
      const map = new Map(response.results.map((result) => [result.client_id, result]))
      for (const item of batch) {
        const result = map.get(item.id)
        if (!result) {
          item.status = 'failed'
          item.error = '预检结果缺失'
          continue
        }
        item.result = result
        if (result.status === 'ok') {
          item.status = 'ready'
        } else if (['duplicate', 'same_name', 'duplicate_and_same_name'].includes(result.status)) {
          item.status = 'conflict'
        } else {
          item.status = 'failed'
          item.error = result.message || '文件校验未通过'
        }
      }
    } catch (e) {
      for (const item of batch) {
        item.status = 'failed'
        item.error = apiErrorMessage(e, '预检失败，请重试')
      }
    }
  }
}

function chooseAction(item: UploadItem, action: ConflictAction): void {
  if (action === 'cancel') {
    item.status = 'failed'
    item.error = '已取消'
    item.action = 'cancel'
    return
  }
  item.action = action
  item.existingId =
    action === 'overwrite' ? item.result?.existing?.document_id || item.result?.duplicate_of?.document_id : undefined
  item.status = 'ready'
  item.error = ''
}

async function startUpload(): Promise<void> {
  const ready = queue.value.filter((item) => item.status === 'ready')
  if (!ready.length || uploading.value) return
  uploading.value = true
  let success = 0
  let failed = 0

  for (const item of ready) {
    item.status = 'uploading'
    item.progress = 0
    try {
      await documentsApi.upload(
        {
          file: item.file,
          uploader_display: uploaderDisplay.value.trim() || undefined,
          conflict_action: item.action,
          existing_document_id: item.existingId
        },
        (event) => {
          if (event.total) item.progress = Math.round((event.loaded / event.total) * 100)
        }
      )
      item.status = 'done'
      item.progress = 100
      success += 1
    } catch (e) {
      item.status = 'failed'
      item.error = apiErrorMessage(e, '上传失败')
      failed += 1
    }
  }

  uploading.value = false
  await refreshList()
  await app.fetchStatus(true)
  if (success > 0) ElMessage.success(`上传完成 ${success} 个文件${failed ? `，失败 ${failed} 个` : ''}`)
  else if (failed > 0) ElMessage.error('上传失败，请查看文件错误')
  if (failed === 0) {
    window.setTimeout(() => {
      uploadVisible.value = false
      queue.value = []
    }, 500)
  }
}

/* ---------------- actions ---------------- */

function canDelete(doc: DocumentSummary): boolean {
  return doc.status === 'available' || doc.status === 'failed'
}

function canReindex(doc: DocumentSummary): boolean {
  return doc.status === 'available' || doc.status === 'failed'
}

function canPreview(doc: DocumentSummary): boolean {
  return doc.status === 'available'
}

async function openPreview(doc: DocumentSummary): Promise<void> {
  previewVisible.value = true
  previewLoading.value = true
  previewDoc.value = null
  previewChunks.value = []
  previewPage.value = 1
  previewTotal.value = 0
  try {
    const [detail, chunks] = await Promise.all([
      documentsApi.detail(doc.id),
      documentsApi.chunks(doc.id, { page: 1, page_size: previewPageSize })
    ])
    previewDoc.value = detail
    previewChunks.value = chunks.items
    previewTotal.value = chunks.total
  } catch (e) {
    ElMessage.error(apiErrorMessage(e, '加载文档详情失败'))
    previewVisible.value = false
  } finally {
    previewLoading.value = false
  }
}

async function loadPreviewChunks(page: number): Promise<void> {
  const doc = previewDoc.value
  if (!doc) return
  previewPage.value = page
  try {
    const chunks = await documentsApi.chunks(doc.id, { page, page_size: previewPageSize })
    previewChunks.value = chunks.items
    previewTotal.value = chunks.total
  } catch (e) {
    ElMessage.error(apiErrorMessage(e, '加载切片失败'))
  }
}

async function downloadDoc(doc: DocumentSummary): Promise<void> {
  try {
    const { blob, filename } = await downloadFile(`/documents/${doc.id}/download?version=active`)
    saveBlob(blob, filename || doc.filename)
  } catch (e) {
    ElMessage.error(apiErrorMessage(e, '下载失败'))
  }
}

async function retryDoc(doc: DocumentSummary): Promise<void> {
  try {
    await documentsApi.retry(doc.id)
    ElMessage.success('已重新加入处理队列')
    await refreshList()
  } catch (e) {
    ElMessage.error(apiErrorMessage(e, '重试失败'))
  }
}

async function reindexDoc(doc: DocumentSummary): Promise<void> {
  try {
    await documentsApi.reindex(doc.id)
    ElMessage.success('已开始重建索引')
    await refreshList()
  } catch (e) {
    ElMessage.error(apiErrorMessage(e, '重建索引失败'))
  }
}

async function removeDoc(doc: DocumentSummary): Promise<void> {
  try {
    await ElMessageBox.confirm(
      '删除后文档进入回收站，7 天内可恢复，确定删除吗？',
      '删除文档',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' }
    )
    await documentsApi.remove(doc.id)
    ElMessage.success('已移入回收站')
    await refreshList()
    await app.fetchStatus(true)
  } catch (e) {
    if (e === 'cancel' || e === 'close') return
    ElMessage.error(apiErrorMessage(e, '删除失败'))
  }
}

async function restoreDoc(doc: DocumentSummary): Promise<void> {
  try {
    await documentsApi.restore(doc.id)
    ElMessage.success('已恢复，正在重新建立索引')
    await refreshList()
    await app.fetchStatus(true)
  } catch (e) {
    ElMessage.error(apiErrorMessage(e, '恢复失败'))
  }
}

async function purgeDoc(doc: DocumentSummary): Promise<void> {
  try {
    await ElMessageBox.confirm(
      '将删除原始文件、全部版本和切片，且不可恢复。确定彻底删除吗？',
      '彻底删除',
      { confirmButtonText: '彻底删除', cancelButtonText: '取消', type: 'error' }
    )
    await documentsApi.purge(doc.id)
    ElMessage.success('已彻底删除')
    await refreshList()
    await app.fetchStatus(true)
  } catch (e) {
    if (e === 'cancel' || e === 'close') return
    ElMessage.error(apiErrorMessage(e, '彻底删除失败'))
  }
}

async function reindexAll(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      '将使用当前 Embedding 模型重建全部可用 / 失败文档的索引，期间旧索引继续可用。确定开始吗？',
      '重建全部索引',
      { confirmButtonText: '开始重建', cancelButtonText: '取消', type: 'warning' }
    )
    const task = await documentsApi.reindexAll()
    reindexTask.value = {
      task_id: task.task_id,
      status: 'queued',
      total: task.accepted_document_ids.length,
      done: 0,
      failed: 0,
      accepted_document_ids: task.accepted_document_ids
    }
    ElMessage.success(`已开始重建 ${task.accepted_document_ids.length} 个文档`)
    await pollReindexTask(task.task_id)
    await refreshList()
  } catch (e) {
    if (e === 'cancel' || e === 'close') return
    ElMessage.error(apiErrorMessage(e, '开始重建失败'))
  }
}

async function pollReindexTask(taskId: string): Promise<void> {
  try {
    const task = await documentsApi.reindexStatus(taskId)
    reindexTask.value = task
    if (task.status === 'queued' || task.status === 'running') {
      window.setTimeout(() => void pollReindexTask(taskId), 2000)
    } else {
      await refreshList()
      await app.fetchStatus(true)
      if (task.status === 'succeeded') ElMessage.success('重建索引完成')
      else ElMessage.error(task.error_message || '重建索引失败')
    }
  } catch (e) {
    ElMessage.error(apiErrorMessage(e, '查询重建进度失败'))
  }
}

function onCommand(command: string, doc: DocumentSummary): void {
  if (command === 'preview') void openPreview(doc)
  if (command === 'download') void downloadDoc(doc)
  if (command === 'retry') void retryDoc(doc)
  if (command === 'reindex') void reindexDoc(doc)
  if (command === 'delete') void removeDoc(doc)
  if (command === 'restore') void restoreDoc(doc)
  if (command === 'purge') void purgeDoc(doc)
}

onMounted(async () => {
  readQuery()
  await Promise.all([app.fetchStatus(true), refreshList()])
})
</script>

<template>
  <div class="page">
    <div class="page-inner">
      <div class="page-head">
        <div>
          <div class="page-title">知识库</div>
          <div class="page-sub">上传文档、查看索引状态、切片预览与回收站</div>
        </div>
        <el-button type="primary" @click="openUpload">上传文档</el-button>
      </div>

      <div v-if="app.status?.reindex_required" class="notice-card">
        <div>
          <strong>Embedding 服务已更换</strong>
          <div class="hint-text">现有索引需要重建后才能使用新模型。</div>
        </div>
        <el-button size="small" @click="reindexAll">重建全部索引</el-button>
      </div>

      <div v-if="reindexTask" class="notice-card">
        <div class="notice-progress">
          <strong>重建索引任务</strong>
          <span class="hint-text">
            {{ reindexTask.status }} · {{ reindexTask.done }} / {{ reindexTask.total }}
            <template v-if="reindexTask.failed"> · 失败 {{ reindexTask.failed }}</template>
          </span>
          <el-progress
            :percentage="reindexTask.total ? Math.round((reindexTask.done / reindexTask.total) * 100) : 0"
            :stroke-width="6"
            :show-text="false"
          />
        </div>
        <el-button v-if="reindexTask.status === 'succeeded' || reindexTask.status === 'failed'" size="small" @click="reindexTask = null">
          关闭
        </el-button>
      </div>

      <div class="toolbar-row">
        <el-input
          v-model="search"
          class="search-input"
          placeholder="搜索标题"
          clearable
          @input="onSearchInput"
          @keyup.enter="applyFilters"
        />
        <el-select v-model="documentsStore.query.status" class="control" style="width: 130px" @change="applyFilters">
          <el-option v-for="option in statusOptions" :key="option.value" :label="option.label" :value="option.value" />
        </el-select>
        <el-select v-model="documentsStore.query.format" class="control" style="width: 130px" @change="applyFilters">
          <el-option v-for="format in formatOptions" :key="format" :label="format === 'all' ? '全部格式' : format" :value="format" />
        </el-select>
        <el-select v-model="documentsStore.query.sort" class="control" style="width: 150px" @change="applyFilters">
          <el-option label="更新时间（新→旧）" value="-updated_at" />
          <el-option label="更新时间（旧→新）" value="updated_at" />
          <el-option label="创建时间（新→旧）" value="-created_at" />
          <el-option label="文件名" value="title" />
        </el-select>
        <span class="hint-text push">共 {{ documentsStore.total }} 条</span>
      </div>

      <el-table v-loading="documentsStore.loading" :data="documentsStore.items" class="doc-table" row-key="id">
        <el-table-column prop="title" label="文件名" min-width="210">
          <template #default="{ row }">
            <div class="doc-title">{{ row.title }}</div>
            <div class="hint-text">{{ row.filename }}</div>
          </template>
        </el-table-column>
        <el-table-column prop="format" label="格式" width="90" />
        <el-table-column label="大小" width="90">
          <template #default="{ row }">{{ formatBytes(row.size_bytes) }}</template>
        </el-table-column>
        <el-table-column prop="uploader" label="上传者" width="100" />
        <el-table-column label="更新时间" width="170">
          <template #default="{ row }">{{ formatDate(row.updated_at) }}</template>
        </el-table-column>
        <el-table-column label="切片数" width="80">
          <template #default="{ row }">{{ row.chunks || '—' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="180">
          <template #default="{ row }">
            <el-tag :type="statusMeta(row.status).tag" size="small" effect="plain">
              {{ statusMeta(row.status).label }}
            </el-tag>
            <div v-if="['queued', 'parsing', 'chunking', 'indexing'].includes(row.status)" class="status-progress">
              <el-progress :percentage="row.progress || 0" :stroke-width="4" :show-text="false" />
              <span class="hint-text">
                {{ row.queue_position ? `队列 ${row.queue_position}` : `${row.progress || 0}%` }}
              </span>
            </div>
            <div v-else-if="row.status === 'failed'" class="error-hint">
              {{ documentErrorText(row.error_code, row.error) }}
            </div>
          </template>
        </el-table-column>
        <el-table-column label="操作" min-width="220">
          <template #default="{ row }">
            <div class="table-actions">
              <el-button v-if="canPreview(row)" link size="small" @click="onCommand('preview', row)">预览</el-button>
              <el-button link size="small" @click="onCommand('download', row)">下载</el-button>
              <el-button v-if="row.status === 'failed'" link size="small" type="primary" @click="onCommand('retry', row)">重试</el-button>
              <el-button v-if="canReindex(row)" link size="small" @click="onCommand('reindex', row)">重建</el-button>
              <el-button v-if="canDelete(row)" link size="small" type="danger" @click="onCommand('delete', row)">删除</el-button>
              <template v-if="row.status === 'deleted'">
                <el-button link size="small" type="primary" @click="onCommand('restore', row)">恢复</el-button>
                <el-button link size="small" type="danger" @click="onCommand('purge', row)">彻底删除</el-button>
              </template>
            </div>
          </template>
        </el-table-column>
        <template #empty>
          <div class="empty-state">
            <strong>{{ documentsStore.query.status === 'trash' ? '回收站是空的' : '还没有文档' }}</strong>
            <span v-if="documentsStore.query.status !== 'trash'">上传第一份资料，开始建立知识库。</span>
            <span v-else>删除的文档会在这里保留 7 天。</span>
          </div>
        </template>
      </el-table>

      <el-pagination
        :current-page="documentsStore.query.page || 1"
        :page-size="documentsStore.query.page_size || 20"
        :total="documentsStore.total"
        layout="prev, pager, next"
        @current-change="onPageChange"
      />

      <!-- 上传弹窗 -->
      <el-dialog v-model="uploadVisible" title="上传文档" width="720px" :close-on-click-modal="false" @close="closeUpload">
        <div class="upload-tip hint-text">
          支持 PDF / Word(.docx) / Markdown，单文件不超过 50MB；上传前会检查重复和同名。
        </div>
        <div class="uploader-row">
          <span class="hint-text">上传者显示名</span>
          <el-input v-model="uploaderDisplay" maxlength="32" placeholder="留空使用当前账号显示名" />
        </div>
        <div
          class="dropzone"
          :class="{ over: dragOver }"
          @click="pickFiles"
          @dragover.prevent="dragOver = true"
          @dragleave.prevent="dragOver = false"
          @drop="onDrop"
        >
          <strong>拖拽文件到此处，或点击选择</strong>
          <span class="hint-text">可一次选择多个文件，单个文件 ≤ 50MB</span>
        </div>
        <input ref="fileInput" type="file" multiple class="hidden-input" @change="onFileChange" />

        <div v-if="queue.length" class="queue">
          <div v-for="item in queue" :key="item.id" class="queue-item">
            <div class="queue-main">
              <div class="queue-name">{{ item.name }}</div>
              <div class="hint-text">{{ formatBytes(item.size) }}</div>
              <div v-if="item.status === 'uploading'" class="queue-progress">
                <el-progress :percentage="item.progress" :stroke-width="4" :show-text="false" />
              </div>
              <div v-if="item.error" class="queue-error">{{ item.error }}</div>
            </div>
            <div class="queue-side">
              <el-tag v-if="item.status === 'checking'" size="small" type="info">校验中</el-tag>
              <el-tag v-else-if="item.status === 'ready'" size="small" type="success">就绪</el-tag>
              <el-tag v-else-if="item.status === 'conflict'" size="small" type="warning">需要确认</el-tag>
              <el-tag v-else-if="item.status === 'uploading'" size="small" type="warning">{{ item.progress }}%</el-tag>
              <el-tag v-else-if="item.status === 'done'" size="small" type="success">完成</el-tag>
              <el-tag v-else size="small" type="danger">失败</el-tag>

              <div v-if="item.status === 'conflict'" class="conflict-actions">
                <el-button
                  v-if="item.result?.allowed_actions.includes('overwrite')"
                  size="small"
                  type="primary"
                  @click="chooseAction(item, 'overwrite')"
                >
                  覆盖
                </el-button>
                <el-button
                  v-if="item.result?.allowed_actions.includes('keep_both')"
                  size="small"
                  @click="chooseAction(item, 'keep_both')"
                >
                  保留两份
                </el-button>
                <el-button size="small" text @click="chooseAction(item, 'cancel')">取消</el-button>
              </div>
            </div>
          </div>
        </div>
        <div v-else class="empty-state">
          <strong>还没有选择文件</strong>
          <span>所有文件会先预检，再逐个上传。</span>
        </div>

        <template #footer>
          <div class="upload-footer">
            <span class="hint-text">
              <template v-if="conflictCount">还有 {{ conflictCount }} 个文件需要确认</template>
              <template v-else>就绪 {{ readyCount }} 个</template>
            </span>
            <div>
              <el-button :disabled="uploading" @click="closeUpload">取消</el-button>
              <el-button type="primary" :loading="uploading" :disabled="!canStartUpload" @click="startUpload">
                开始上传{{ readyCount ? ` (${readyCount})` : '' }}
              </el-button>
            </div>
          </div>
        </template>
      </el-dialog>

      <!-- 文档详情 -->
      <el-dialog v-model="previewVisible" :title="previewDoc?.title || '文档详情'" width="780px">
        <div v-loading="previewLoading">
          <template v-if="previewDoc">
            <div class="detail-grid">
              <div><span>格式</span><strong>{{ previewDoc.format }}</strong></div>
              <div><span>大小</span><strong>{{ formatBytes(previewDoc.size_bytes) }}</strong></div>
              <div><span>上传者</span><strong>{{ previewDoc.uploader }}</strong></div>
              <div><span>状态</span><strong>{{ statusMeta(previewDoc.status).label }}</strong></div>
              <div><span>切片数</span><strong>{{ previewDoc.chunks }}</strong></div>
              <div><span>更新时间</span><strong>{{ formatDate(previewDoc.updated_at) }}</strong></div>
            </div>
            <div v-if="previewDoc.parse_meta" class="parse-meta">
              <span>页数：{{ previewDoc.parse_meta.page_count ?? '—' }}</span>
              <span>段落：{{ previewDoc.parse_meta.paragraph_count ?? '—' }}</span>
              <span>字符：{{ previewDoc.parse_meta.char_count }}</span>
              <span>解析器：{{ previewDoc.parse_meta.parser }}</span>
            </div>
            <div v-if="previewDoc.error" class="page-error">{{ documentErrorText(previewDoc.error_code, previewDoc.error) }}</div>
            <h4 class="chunk-title">切片预览</h4>
            <div v-if="previewChunks.length" class="chunk-list">
              <div v-for="chunk in previewChunks" :key="chunk.id" class="chunk-card">
                <div class="hint-text">
                  切片 {{ chunk.seq }}
                  <template v-if="chunk.page"> · 第 {{ chunk.page }} 页</template>
                  <template v-if="chunk.paragraph"> · {{ chunk.paragraph }}</template>
                </div>
                <p>{{ chunk.text }}</p>
              </div>
            </div>
            <div v-else class="empty-state"><span>没有可预览的切片</span></div>
            <el-pagination
              v-if="previewTotal > previewPageSize"
              :current-page="previewPage"
              :page-size="previewPageSize"
              :total="previewTotal"
              layout="prev, pager, next"
              @current-change="loadPreviewChunks"
            />
          </template>
        </div>
      </el-dialog>
    </div>
  </div>
</template>

<style scoped>
.notice-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
  padding: 14px 16px;
  border: 1px solid #f0d9c8;
  border-radius: var(--kb-radius-md);
  background: #fdf8f2;
  font-size: 13px;
}

.notice-progress {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.search-input {
  width: 230px;
}

.doc-title {
  color: var(--kb-text);
  font-weight: 500;
}

.status-progress {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 5px;
  width: 130px;
}

.status-progress :deep(.el-progress) {
  flex: 1;
}

.error-hint {
  margin-top: 4px;
  color: #b45545;
  font-size: 12px;
  line-height: 1.4;
}

.upload-tip {
  margin-bottom: 14px;
}

.uploader-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
}

.uploader-row .el-input {
  flex: 1;
}

.dropzone {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 28px;
  border: 1px dashed var(--kb-border);
  border-radius: var(--kb-radius-md);
  background: var(--kb-bg);
  color: var(--kb-text-secondary);
  font-size: 14px;
  cursor: pointer;
  transition: all 0.2s;
}

.dropzone:hover,
.dropzone.over {
  border-color: var(--kb-accent);
  background: var(--kb-accent-soft);
  color: var(--kb-accent);
}

.hidden-input {
  display: none;
}

.queue {
  margin-top: 16px;
  max-height: 300px;
  overflow-y: auto;
  border: 1px solid var(--kb-border);
  border-radius: var(--kb-radius-md);
}

.queue-item {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--kb-border);
}

.queue-item:last-child {
  border-bottom: 0;
}

.queue-main {
  flex: 1;
  min-width: 0;
}

.queue-name {
  overflow: hidden;
  color: var(--kb-text);
  font-size: 13px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.queue-progress {
  margin-top: 6px;
  width: 240px;
}

.queue-error {
  margin-top: 4px;
  color: #b45545;
  font-size: 12px;
}

.queue-side {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 8px;
}

.conflict-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 6px;
}

.upload-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.detail-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  margin-bottom: 16px;
}

.detail-grid div {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--kb-bg);
  font-size: 13px;
}

.detail-grid span {
  color: var(--kb-text-tertiary);
  font-size: 12px;
}

.parse-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 16px;
  color: var(--kb-text-secondary);
  font-size: 12px;
}

.chunk-title {
  margin: 16px 0 10px;
  font-size: 14px;
  font-weight: 600;
}

.chunk-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 360px;
  overflow-y: auto;
}

.chunk-card {
  padding: 12px;
  border-radius: 8px;
  background: var(--kb-bg);
}

.chunk-card p {
  margin: 6px 0 0;
  color: var(--kb-text);
  font-size: 13px;
  line-height: 1.65;
  white-space: pre-wrap;
}

@media (max-width: 1100px) {
  .search-input {
    width: 180px;
  }

  .detail-grid {
    grid-template-columns: 1fr 1fr;
  }
}
</style>