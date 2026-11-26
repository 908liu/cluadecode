<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElForm, ElFormItem, ElInput, ElButton, type FormInstance, type FormRules } from 'element-plus'
import { authApi } from '@/api/auth'
import { apiErrorMessage } from '@/api/http'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

const formRef = ref<FormInstance>()
const form = reactive({ username: '', password: '' })
const error = ref('')
const registrationAllowed = ref(true)

onMounted(async () => {
  try {
    const status = await authApi.registrationStatus()
    registrationAllowed.value = status.registration_allowed
  } catch {
    // 后端不可用时保留入口，由注册页处理具体错误
  }
})

const rules: FormRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }]
}

function safeRedirect(): string {
  const value = typeof route.query.redirect === 'string' ? route.query.redirect : ''
  if (value.startsWith('/') && !value.startsWith('//') && !value.startsWith('/login')) return value
  return '/chat'
}

async function submit(): Promise<void> {
  error.value = ''
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  try {
    await auth.login({ username: form.username.trim(), password: form.password })
    await router.replace(safeRedirect())
  } catch (e) {
    error.value = apiErrorMessage(e, '登录失败')
  }
}

</script>

<template>
  <div class="auth-page">
    <div class="auth-card">
      <div class="auth-brand">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="var(--kb-accent)" aria-hidden="true">
          <path d="M12 2L14.5 9.5L22 12L14.5 14.5L12 22L9.5 14.5L2 12L9.5 9.5L12 2Z" />
        </svg>
        <span>医知助手</span>
      </div>

      <div class="auth-head">
        <h1>欢迎回来</h1>
        <p>检索医疗指南与流程资料，并查看原文依据。</p>
      </div>

      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="submit">
        <el-form-item label="用户名" prop="username">
          <el-input v-model="form.username" autocomplete="username" placeholder="输入用户名" />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            autocomplete="current-password"
            show-password
            placeholder="输入密码"
            @keyup.enter="submit"
          />
        </el-form-item>

        <div v-if="error" class="auth-error">{{ error }}</div>

        <el-button type="primary" class="auth-submit" :loading="auth.loading" @click="submit">登录</el-button>
      </el-form>

      <div v-if="registrationAllowed" class="auth-foot">
        <span class="hint-text">还没有账号？</span>
        <router-link to="/register">创建账号</router-link>
      </div>
    </div>
  </div>
</template>

<style scoped>
.auth-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: 24px;
  background:
    radial-gradient(circle at 20% 10%, rgba(217, 119, 87, 0.08), transparent 34%),
    var(--kb-bg);
}

.auth-card {
  width: 100%;
  max-width: 420px;
  padding: 34px 34px 28px;
  border: 1px solid var(--kb-border);
  border-radius: 20px;
  background: var(--kb-surface);
  box-shadow: 0 20px 60px rgba(28, 28, 26, 0.08);
}

.auth-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 30px;
  font-size: 16px;
  font-weight: 600;
}

.auth-head h1 {
  margin: 0;
  font-size: 26px;
  font-weight: 600;
  letter-spacing: -0.4px;
}

.auth-head p {
  margin: 8px 0 26px;
  color: var(--kb-text-tertiary);
  font-size: 13px;
}

.auth-error {
  margin-bottom: 14px;
  padding: 9px 12px;
  border-radius: 8px;
  background: #fdf5f4;
  color: #9b3d32;
  font-size: 13px;
}

.auth-submit {
  width: 100%;
  height: 42px;
  margin-top: 4px;
  border-radius: 999px;
}

.auth-foot {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  margin-top: 22px;
  font-size: 13px;
}
</style>