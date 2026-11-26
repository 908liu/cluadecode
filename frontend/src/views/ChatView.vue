<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { apiErrorMessage, downloadFile, saveBlob } from '@/api/http'
import { qaApi } from '@/api/qa'
import { useAppStore } from '@/stores/app'
import AnswerContent from '@/components/AnswerContent.vue'
import CitationCard from '@/components/CitationCard.vue'
import { refusalLabels } from '@/utils/format'
import type { Citation, RefusalReason } from '@/types/api'

interface ChatMessage {
  id: number
  role: 'user' | 'assistant'
  text: string
  citations?: Citation[]
  refused?: boolean
  refusalReason?: RefusalReason | null
  latency?: number
  model?: string
  streaming?: boolean
}

const app = useAppStore()
const router = useRouter()

const input = ref('')
const asking = ref(false)
const messages = ref<ChatMessage[]>([])
const messagesEl = ref<HTMLElement | null>(null)
const error = ref('')
const lastQuestion = ref('')
const activeCitation = ref('')
let messageId = 0

const modelLabel = computed(() => {
  const service = app.status?.types.chat.service
  return service ? service.model : '未配置模型'
})

const missingReason = computed(() => {
  if (!app.status) return app.error ? '无法连接服务，请确认后端已启动。' : '正在获取系统状态…'
  if (!app.status.types.chat.configured) return '还没有配置对话服务，请先去设置。'
  if (!app.status.types.embedding.configured) return '还没有配置 Embedding 服务，请先去设置。'
  if (app.status.available_documents === 0) return '知识库还没有可用文档，先上传一份资料。'
  return ''
})

const canAsk = computed(() => !missingReason.value)
const canSend = computed(
  () => !asking.value && canAsk.value && input.value.trim().length > 0 && input.value.length <= 2000
)
const charCount = computed(() => input.value.length)

function scrollToBottom(): void {
  void nextTick(() => {
    if (messagesEl.value) messagesEl.value.scrollTop = messagesEl.value.scrollHeight
  })
}

let scrollPending = false
function scheduleScroll(): void {
  if (scrollPending) return
  scrollPending = true
  window.requestAnimationFrame(() => {
    scrollPending = false
    scrollToBottom()
  })
}

function scrollToCitation(message: ChatMessage, citationIndex: number): void {
  const key = `${message.id}-${citationIndex}`
  activeCitation.value = key
  const element = document.getElementById(`citation-${key}`)
  element?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
}

async function downloadCitation(citation: Citation): Promise<void> {
  try {
    const path = citation.download_url || `/documents/${citation.document_id}/download?version=${citation.version}`
    const { blob, filename } = await downloadFile(path)
    saveBlob(blob, filename || `${citation.document_title}-v${citation.version}`)
  } catch (e) {
    ElMessage.error(apiErrorMessage(e, '下载失败'))
  }
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return
  event.preventDefault()
  void send()
}

async function send(): Promise<void> {
  if (!canSend.value) return
  const question = input.value.trim()
  if (!question) return

  error.value = ''
  lastQuestion.value = question
  input.value = ''
  asking.value = true
  messages.value.push({ id: ++messageId, role: 'user', text: question })
  messages.value.push({
    id: ++messageId,
    role: 'assistant',
    text: '',
    streaming: true
  })
  const assistant = messages.value[messages.value.length - 1]
  scrollToBottom()

  try {
    await qaApi.askStream(question, {
      onDelta: (text) => {
        assistant.text += text
        scheduleScroll()
      },
      onFinal: (response) => {
        assistant.text = response.answer
        assistant.citations = response.citations
        assistant.refused = response.refused
        assistant.refusalReason = response.refusal_reason
        assistant.latency = response.latency_ms
        assistant.model = response.model
        assistant.streaming = false
        scrollToBottom()
      }
    })
  } catch (e) {
    assistant.streaming = false
    if (!assistant.text.trim()) {
      messages.value = messages.value.filter((item) => item.id !== assistant.id)
    }
    error.value = apiErrorMessage(e, '回答失败，请稍后重试')
  } finally {
    assistant.streaming = false
    asking.value = false
    scrollToBottom()
  }
}

function retry(): void {
  if (!lastQuestion.value) return
  input.value = lastQuestion.value
  void send()
}

function goSettings(): void {
  void router.push('/settings/api')
}

function goDocuments(): void {
  void router.push('/documents')
}

onMounted(async () => {
  await app.fetchStatus(true)
})
</script>

<template>
  <div class="chat-page">
    <div class="chat-head">
      <div class="head-left">
        <div class="model-pill">
          <span class="model-dot" />
          模型：{{ modelLabel }}
        </div>
      </div>
      <span class="hint-text">
        单轮问答 · 回答必须带引用，无依据会拒答
      </span>
    </div>

    <div ref="messagesEl" class="messages">
      <div v-if="!messages.length" class="welcome">
        <div class="welcome-mark">✦</div>
        <h2>医疗资料问答</h2>
        <p class="hint-text">
          上传指南或流程资料后提问，我会基于原文回答并标注出处；没有依据时会明确拒答。
        </p>
        <div v-if="missingReason" class="setup-card">
          <p>{{ missingReason }}</p>
          <div class="setup-actions">
            <el-button v-if="app.status && !app.status.types.chat.configured" type="primary" @click="goSettings">去配置模型</el-button>
            <el-button v-else-if="app.status && app.status.available_documents === 0" @click="goDocuments">去上传文档</el-button>
          </div>
        </div>
      </div>

      <div v-for="message in messages" :key="message.id" class="message" :class="message.role">
        <div class="message-role">{{ message.role === 'user' ? '你' : 'AI 助手' }}</div>
        <div v-if="message.role === 'user'" class="user-bubble">{{ message.text }}</div>
        <div v-else class="assistant-bubble">
          <div v-if="message.refused" class="refusal">
            <span class="refusal-label">拒答</span>
            {{ message.refusalReason ? refusalLabels[message.refusalReason] : '依据不足' }}
          </div>
          <div v-if="message.streaming && !message.text" class="streaming-placeholder">
            <span class="dot" /><span class="dot" /><span class="dot" />
            正在检索并生成答案…
          </div>
          <template v-else>
            <AnswerContent
              :text="message.text"
              @cite="(index) => scrollToCitation(message, index - 1)"
            />
            <span v-if="message.streaming" class="stream-cursor">▍</span>
          </template>
          <div v-if="message.citations?.length" class="citation-list">
            <CitationCard
              v-for="(citation, index) in message.citations"
              :id="`citation-${message.id}-${index}`"
              :key="citation.chunk_id"
              :citation="citation"
              :active="activeCitation === `${message.id}-${index}`"
              @download="downloadCitation"
            />
          </div>
          <div class="answer-meta">
            <span v-if="message.model">模型：{{ message.model }}</span>
            <span v-if="message.latency != null">{{ message.latency }} ms</span>
          </div>
        </div>
      </div>

      <div v-if="error" class="chat-error">
        <span>{{ error }}</span>
        <el-button text type="primary" @click="retry">重试</el-button>
      </div>
    </div>

    <div class="composer">
      <div class="composer-box">
        <textarea
          v-model="input"
          rows="1"
          maxlength="2000"
          placeholder="输入问题，Enter 发送，Shift + Enter 换行"
          @keydown="onKeydown"
        />
        <div class="composer-bar">
          <span class="hint-text">
            {{ missingReason || (charCount > 0 ? `${charCount} / 2000` : '单轮问答') }}
          </span>
          <el-button type="primary" :disabled="!canSend" :loading="asking" @click="send">发送</el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--kb-bg);
}

.chat-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 15%;
  border-bottom: 1px solid var(--kb-border);
}

.head-left {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}

.model-pill {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 26px;
  padding: 0 11px;
  border-radius: 999px;
  background: var(--kb-accent-soft);
  color: var(--kb-accent);
  font-size: 12px;
  font-weight: 500;
}

.model-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--kb-accent);
}

.messages {
  flex: 1;
  overflow-y: auto;
  padding: 34px 15% 16px;
}

.welcome {
  max-width: 680px;
  margin: 8vh auto 0;
  text-align: center;
}

.welcome-mark {
  color: var(--kb-accent);
  font-size: 26px;
}

.welcome h2 {
  margin: 10px 0 8px;
  font-size: 26px;
  font-weight: 600;
}

.welcome p {
  margin: 0 auto;
  max-width: 520px;
}

.setup-card {
  margin: 24px auto 0;
  max-width: 480px;
  padding: 18px;
  border: 1px solid var(--kb-border);
  border-radius: var(--kb-radius-md);
  background: var(--kb-surface);
}

.setup-card p {
  margin: 0 0 14px;
  color: var(--kb-text-secondary);
  font-size: 13px;
}

.setup-actions {
  display: flex;
  justify-content: center;
}

.message {
  max-width: 860px;
  margin: 0 auto 26px;
}

.message-role {
  margin-bottom: 9px;
  color: var(--kb-text-tertiary);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.4px;
}

.message.user {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
}

.message.user .message-role {
  text-align: right;
}

.user-bubble {
  max-width: 80%;
  padding: 12px 18px;
  border: 1px solid var(--kb-border);
  border-radius: var(--kb-radius-lg) var(--kb-radius-lg) 4px var(--kb-radius-lg);
  background: var(--kb-surface);
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.02);
  font-size: 15px;
  line-height: 1.65;
  white-space: pre-wrap;
}

.assistant-bubble {
  font-size: 15px;
  line-height: 1.75;
}

.retrieval-line {
  margin-bottom: 8px;
  padding-left: 8px;
  border-left: 2px solid var(--kb-border);
  color: var(--kb-text-tertiary);
  font-size: 12px;
}

.refusal {
  margin-bottom: 10px;
  padding: 10px 12px;
  border-left: 2px solid var(--kb-accent);
  border-radius: 0 8px 8px 0;
  background: var(--kb-accent-soft);
  color: #8a4a38;
  font-size: 13px;
}

.refusal-label {
  margin-right: 8px;
  font-weight: 600;
}

.citation-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 16px;
}

.answer-meta {
  display: flex;
  gap: 14px;
  margin-top: 10px;
  color: var(--kb-text-tertiary);
  font-size: 12px;
}

.loading-bubble,
.streaming-placeholder {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--kb-text-tertiary);
  font-size: 14px;
}

.stream-cursor {
  display: inline-block;
  margin-left: 2px;
  color: var(--kb-accent);
  animation: blink 1s steps(2, start) infinite;
}

@keyframes blink {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0;
  }
}

.dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--kb-accent);
  animation: pulse 1.2s infinite;
}

.dot:nth-child(2) {
  animation-delay: 0.15s;
}

.dot:nth-child(3) {
  animation-delay: 0.3s;
}

@keyframes pulse {
  0%,
  100% {
    opacity: 0.3;
    transform: translateY(0);
  }
  50% {
    opacity: 1;
    transform: translateY(-2px);
  }
}

.chat-error {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  max-width: 860px;
  margin: 0 auto;
  padding: 10px 14px;
  border-radius: 8px;
  background: #fdf5f4;
  color: #9b3d32;
  font-size: 13px;
}

.composer {
  padding: 12px 15% 26px;
}

.composer-box {
  overflow: hidden;
  border: 1px solid var(--kb-border);
  border-radius: var(--kb-radius-lg);
  background: var(--kb-surface);
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.03);
  transition: border-color 0.2s, box-shadow 0.2s;
}

.composer-box:focus-within {
  border-color: var(--kb-accent);
  box-shadow: 0 0 0 4px var(--kb-accent-soft), 0 6px 24px rgba(0, 0, 0, 0.05);
}

.composer textarea {
  display: block;
  width: 100%;
  min-height: 58px;
  max-height: 180px;
  padding: 16px 18px 6px;
  border: 0;
  outline: none;
  resize: none;
  background: transparent;
  color: var(--kb-text);
  font-family: inherit;
  font-size: 15px;
  line-height: 1.55;
}

.composer textarea::placeholder {
  color: var(--kb-text-tertiary);
}

.composer-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 12px 12px 18px;
}

@media (max-width: 1100px) {
  .chat-head,
  .messages,
  .composer {
    padding-left: 6%;
    padding-right: 6%;
  }
}
</style>