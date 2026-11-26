import { defineStore } from 'pinia'
import { authApi } from '@/api/auth'
import { TOKEN_KEY, USER_KEY } from '@/api/http'
import type { AuthUser, LoginRequest, LoginResponse, RegisterRequest } from '@/types/api'

function readUser(): AuthUser | null {
  try {
    const raw = localStorage.getItem(USER_KEY)
    return raw ? (JSON.parse(raw) as AuthUser) : null
  } catch {
    return null
  }
}

export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: localStorage.getItem(TOKEN_KEY) || '',
    user: readUser() as AuthUser | null,
    loading: false
  }),
  getters: {
    isLoggedIn: (state) => Boolean(state.token),
    displayName: (state) => state.user?.display_name || state.user?.username || '用户'
  },
  actions: {
    setAuth(payload: LoginResponse): void {
      this.token = payload.access_token
      this.user = payload.user
      localStorage.setItem(TOKEN_KEY, payload.access_token)
      localStorage.setItem(USER_KEY, JSON.stringify(payload.user))
    },
    clear(): void {
      this.token = ''
      this.user = null
      localStorage.removeItem(TOKEN_KEY)
      localStorage.removeItem(USER_KEY)
    },
    async login(payload: LoginRequest): Promise<void> {
      this.loading = true
      try {
        const response = await authApi.login(payload)
        this.setAuth(response)
      } finally {
        this.loading = false
      }
    },
    async register(payload: RegisterRequest): Promise<void> {
      this.loading = true
      try {
        const response = await authApi.register(payload)
        this.setAuth(response)
      } finally {
        this.loading = false
      }
    },
    async fetchMe(): Promise<void> {
      if (!this.token) return
      try {
        this.user = await authApi.me()
        localStorage.setItem(USER_KEY, JSON.stringify(this.user))
      } catch {
        // 401 由全局拦截器处理
      }
    },
    async logout(): Promise<void> {
      try {
        await authApi.logout()
      } catch {
        // 无状态 JWT 下登出失败也要清本地
      } finally {
        this.clear()
      }
    }
  }
})