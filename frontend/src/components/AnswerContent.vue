<script setup lang="ts">
import { computed } from 'vue'

interface Segment {
  type: 'text' | 'cite'
  text: string
  index?: number
}

const props = defineProps<{ text: string }>()
const emit = defineEmits<{ (event: 'cite', index: number): void }>()

const segments = computed<Segment[]>(() => {
  const source = props.text || ''
  const result: Segment[] = []
  let buffer = ''
  let i = 0

  while (i < source.length) {
    if (source.charAt(i) === '[') {
      let j = i + 1
      while (j < source.length && source.charAt(j) >= '0' && source.charAt(j) <= '9') j++
      if (j > i + 1 && source.charAt(j) === ']') {
        if (buffer) {
          result.push({ type: 'text', text: buffer })
          buffer = ''
        }
        result.push({ type: 'cite', text: source.slice(i + 1, j), index: Number(source.slice(i + 1, j)) })
        i = j + 1
        continue
      }
    }
    buffer += source.charAt(i)
    i++
  }

  if (buffer) result.push({ type: 'text', text: buffer })
  return result
})
</script>

<template>
  <span class="answer-content">
    <template v-for="(segment, index) in segments" :key="index">
      <span v-if="segment.type === 'text'">{{ segment.text }}</span>
      <button
        v-else
        type="button"
        class="cite-pill"
        @click="emit('cite', segment.index || 0)"
      >
        {{ segment.index }}
      </button>
    </template>
  </span>
</template>

<style scoped>
.answer-content {
  white-space: pre-wrap;
  line-height: 1.75;
}

.cite-pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 22px;
  margin: 0 3px;
  padding: 0 8px;
  border: 1px solid transparent;
  border-radius: 999px;
  background: var(--kb-accent-soft);
  color: var(--kb-accent);
  font-size: 12px;
  font-weight: 600;
  vertical-align: middle;
  cursor: pointer;
  transition: all 0.2s ease;
}

.cite-pill:hover {
  border-color: var(--kb-accent);
  background: #fff;
  transform: translateY(-1px);
}
</style>