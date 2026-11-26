<script setup lang="ts">
import type { Citation } from '@/types/api'

defineProps<{ citation: Citation; active?: boolean }>()
const emit = defineEmits<{ (event: 'download', citation: Citation): void }>()
</script>

<template>
  <div class="citation-card" :class="{ active }">
    <div class="citation-head">
      <span class="citation-title">{{ citation.document_title }}</span>
      <span class="citation-meta">v{{ citation.version }}</span>
    </div>
    <div class="citation-meta">
      {{ citation.page ? `第 ${citation.page} 页` : '—' }}
      <span v-if="citation.paragraph"> · {{ citation.paragraph }}</span>
    </div>
    <div class="citation-text">{{ citation.snippet }}</div>
    <div class="citation-foot">
      <span>上传者：{{ citation.uploader || '匿名' }}</span>
      <button type="button" class="download-link" @click="emit('download', citation)">下载原文</button>
    </div>
  </div>
</template>

<style scoped>
.citation-card {
  padding: 14px 16px;
  border: 1px solid var(--kb-border);
  border-radius: var(--kb-radius-md);
  background: var(--kb-surface);
  transition: border-color 0.2s, box-shadow 0.2s;
}

.citation-card.active {
  border-color: var(--kb-accent);
  box-shadow: 0 0 0 3px var(--kb-accent-soft);
}

.citation-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 4px;
}

.citation-title {
  color: var(--kb-text);
  font-size: 13px;
  font-weight: 500;
}

.citation-meta {
  color: var(--kb-text-tertiary);
  font-size: 12px;
}

.citation-text {
  margin-top: 6px;
  color: var(--kb-text);
  font-size: 13px;
  line-height: 1.65;
}

.citation-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 10px;
  color: var(--kb-text-tertiary);
  font-size: 12px;
}

.download-link {
  padding: 0;
  border: 0;
  background: none;
  color: var(--kb-accent);
  font-size: 12px;
  cursor: pointer;
}
</style>