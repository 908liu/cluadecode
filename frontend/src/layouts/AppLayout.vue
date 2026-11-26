<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ChatDotRound, DataAnalysis, Files, Setting, SwitchButton } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { useAppStore } from '@/stores/app'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const app = useAppStore()

const navItems = [
  { name: 'chat', label: '对话', icon: ChatDotRound, path: '/chat' },
  { name: 'documents', label: '知识库管理', icon: Files, path: '/documents' },
  { name: 'evaluation', label: '评测面板', icon: DataAnalysis, path: '/evaluation' }
]

const title = computed(() => (typeof route.meta.title === 'string' ? route.meta.title : '医知助手'))
const initial = computed(() => auth.displayName.slice(0, 1).toUpperCase())
const isSettings = computed(() => route.name === 'settings-api')

function go(path: string): void {
  if (route.path !== path) void router.push(path)
}

function handleUnauthorized(): void {
  auth.clear()
  const redirect = route.fullPath
  void router.replace({ name: 'login', query: redirect.startsWith('/login') ? {} : { redirect } })
}

function handleStorage(event: StorageEvent): void {
  if (event.key === 'kb_token' && !event.newValue) handleUnauthorized()
}

async function handleLogout(): Promise<void> {
  await auth.logout()
  void router.replace({ name: 'login' })
}

onMounted(async () => {
  window.addEventListener('kb:unauthorized', handleUnauthorized)
  window.addEventListener('storage', handleStorage)
  if (!app.status) await app.fetchStatus()
  if (!auth.user) await auth.fetchMe()
})

onBeforeUnmount(() => {
  window.removeEventListener('kb:unauthorized', handleUnauthorized)
  window.removeEventListener('storage', handleStorage)
})
</script>

<template>
  <div class="layout">
    <aside class="sidebar">
      <div class="brand">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="var(--kb-accent)" aria-hidden="true">
          <path d="M12 2L14.5 9.5L22 12L14.5 14.5L12 22L9.5 14.5L2 12L9.5 9.5L12 2Z" />
        </svg>
        <span>医知助手</span>
      </div>

      <nav class="nav-list">
        <button
          v-for="item in navItems"
          :key="item.name"
          type="button"
          class="nav-item"
          :class="{ active: route.name === item.name }"
          @click="go(item.path)"
        >
          <el-icon class="nav-icon"><component :is="item.icon" /></el-icon>
          <span>{{ item.label }}</span>
        </button>
      </nav>

      <div class="sidebar-bottom">
        <el-dropdown trigger="click" @command="handleLogout">
          <div class="user-profile">
            <div class="avatar">{{ initial }}</div>
            <div class="user-info">
              <span class="user-name">{{ auth.displayName }}</span>
              <span class="user-plan">{{ auth.user?.username || '—' }}</span>
            </div>
          </div>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="logout">
                <el-icon><SwitchButton /></el-icon>
                退出登录
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>

        <button type="button" class="icon-btn" title="设置" @click="go('/settings/api')">
          <el-icon><Setting /></el-icon>
        </button>
      </div>
    </aside>

    <div class="main">
      <header class="topbar">
        <span class="topbar-title">{{ title }}</span>
        <span v-if="isSettings" class="hint-text">API 配置</span>
        <span v-else class="hint-text">医疗指南与流程知识库</span>
      </header>

      <div class="content">
        <router-view />
      </div>
    </div>
  </div>
</template>

<style scoped>
.layout {
  display: flex;
  height: 100vh;
  overflow: hidden;
  background: var(--kb-bg);
}

.sidebar {
  width: 250px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  padding: 20px 16px;
  border-right: 1px solid var(--kb-border);
  background: var(--kb-bg);
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 4px 8px 24px;
  font-size: 16px;
  font-weight: 600;
}

.nav-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 10px 12px;
  border: 1px solid transparent;
  border-radius: 10px;
  background: transparent;
  color: var(--kb-text-secondary);
  font-size: 14px;
  text-align: left;
  cursor: pointer;
  transition: all 0.2s ease;
}

.nav-item:hover {
  background: var(--kb-hover);
  color: var(--kb-text);
}

.nav-item.active {
  border-color: rgba(0, 0, 0, 0.04);
  background: var(--kb-surface);
  color: var(--kb-text);
  font-weight: 500;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.02), 0 2px 8px rgba(0, 0, 0, 0.04);
}

.nav-icon {
  font-size: 18px;
}

.sidebar-bottom {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 4px;
  margin-top: auto;
  padding-top: 16px;
  border-top: 1px solid var(--kb-border);
}

.user-profile {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
  min-width: 0;
  padding: 6px;
  border-radius: 8px;
  cursor: pointer;
  overflow: hidden;
}

.user-profile:hover {
  background: var(--kb-hover);
}

.avatar {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  border-radius: 50%;
  background: #e5e5e1;
  color: var(--kb-text-secondary);
  font-size: 12px;
  font-weight: 600;
}

.user-info {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.user-name {
  overflow: hidden;
  color: var(--kb-text);
  font-size: 13px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-plan {
  overflow: hidden;
  color: var(--kb-text-tertiary);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.icon-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  flex-shrink: 0;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--kb-text-tertiary);
  cursor: pointer;
}

.icon-btn:hover {
  background: var(--kb-hover);
  color: var(--kb-text);
}

.main {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 54px;
  flex-shrink: 0;
  padding: 0 32px;
  border-bottom: 1px solid var(--kb-border);
  background: rgba(247, 246, 243, 0.86);
  backdrop-filter: blur(10px);
}

.topbar-title {
  font-size: 14px;
  font-weight: 600;
}

.content {
  flex: 1;
  min-height: 0;
}

@media (max-width: 900px) {
  .sidebar {
    width: 216px;
  }

  .topbar {
    padding: 0 20px;
  }
}
</style>