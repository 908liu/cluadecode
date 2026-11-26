<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, type FormInstance, type FormItemRule, type FormRules } from 'element-plus'
import { authApi } from '@/api/auth'
import { ApiError, apiErrorMessage } from '@/api/http'
import { useAuthStore } from '@/stores/auth'
import type { RegistrationStatus } from '@/types/api'

const router = useRouter()
const auth = useAuthStore()

const formRef = ref<FormInstance>()
const status = ref<RegistrationStatus | null>(null)
const form = reactive({
  username: '',
  password: '',
  invite_code: ''
})
const error = ref('')

const inviteRule: FormItemRule = {
  validator: (_rule, value, callback) => {
    if (status.value?.invite_required && !String(value || '').trim()) callback(new Error('请输入邀请码'))
    else callback()
  },
  trigger: 'blur'
}

const rules: FormRules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { max: 64, message: '用户名不能超过 64 个字符', trigger: 'blur' }
  ],
  invite_code: [{ validator: inviteRule.validator, trigger: 'blur' }],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 8, max: 128, message: '密码长度需为 8~128 个字符', trigger: 'blur' }
  ]
}

onMounted(async () => {
  try {
    status.value = await authApi.registrationStatus()
  } catch {
    // 后端不可用时保留表单，由提交阶段返回具体错误
  }
})

async function submit(): Promise<void> {
  error.value = ''
  if (status.value && !status.value.registration_allowed) {
    error.value = '当前站点已关闭注册'
    return
  }
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  try {
    await auth.register({
      username: form.username.trim(),
      password: form.password,
      invite_code: form.invite_code.trim() || undefined
    })
    ElMessage.success('账号创建成功')
    await router.replace('/chat')
  } catch (e) {
    if (e instanceof ApiError && e.code === 40908) {
      error.value = '用户名已存在，请换一个用户名'
      return
    }
    error.value = apiErrorMessage(e, '注册失败')
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
        <h1>创建账号</h1>
        <p>创建账号后即可管理自己的医疗资料与问答记录。</p>
      </div>

      <div v-if="status && !status.registration_allowed" class="auth-notice">
        当前站点已关闭注册，请联系管理员开通账号。
      </div>

      <el-form
        v-if="!status || status.registration_allowed"
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
        @submit.prevent="submit"
      >
        <el-form-item label="用户名" prop="username">
          <el-input v-model="form.username" autocomplete="username" placeholder="1~64 个字符" />
        </el-form-item>
        <el-form-item v-if="status?.invite_required" label="邀请码" prop="invite_code">
          <el-input v-model="form.invite_code" autocomplete="off" placeholder="请输入管理员提供的邀请码" />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            autocomplete="new-password"
            show-password
            placeholder="至少 8 个字符"
            @keyup.enter="submit"
          />
        </el-form-item>

        <div v-if="error" class="auth-error">{{ error }}</div>

        <el-button type="primary" class="auth-submit" :loading="auth.loading" @click="submit">创建账号并登录</el-button>
      </el-form>

      <div class="auth-foot">
        <span class="hint-text">已经有账号？</span>
        <router-link to="/login">去登录</router-link>
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
  max-width: 430px;
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
  line-height: 1.6;
}

.auth-error {
  margin-bottom: 14px;
  padding: 9px 12px;
  border-radius: 8px;
  background: #fdf5f4;
  color: #9b3d32;
  font-size: 13px;
}

.auth-notice {
  margin-bottom: 14px;
  padding: 12px 14px;
  border: 1px solid var(--kb-border);
  border-radius: 10px;
  color: var(--kb-text-secondary);
  font-size: 13px;
  line-height: 1.6;
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