<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()
</script>

<template>
  <div class="not-found">
    <div class="code">404</div>
    <h1>页面不存在</h1>
    <p class="hint-text">链接可能已失效，或者地址输入有误。</p>
    <el-button type="primary" @click="router.replace('/chat')">返回对话</el-button>
  </div>
</template>

<style scoped>
.not-found {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: 24px;
  text-align: center;
}

.code {
  color: var(--kb-accent);
  font-size: 64px;
  font-weight: 600;
  letter-spacing: -2px;
}

h1 {
  margin: 10px 0 6px;
  font-size: 22px;
  font-weight: 600;
}

p {
  margin: 0 0 22px;
}
</style>