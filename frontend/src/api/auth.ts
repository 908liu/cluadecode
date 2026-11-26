import { request } from './http'
import type {
  AuthUser,
  LoginRequest,
  LoginResponse,
  RegisterRequest,
  RegisterResponse,
  RegistrationStatus
} from '@/types/api'

export const authApi = {
  registrationStatus(): Promise<RegistrationStatus> {
    return request({ url: '/auth/registration-status', method: 'get' })
  },
  register(payload: RegisterRequest): Promise<RegisterResponse> {
    return request({ url: '/auth/register', method: 'post', data: payload })
  },
  login(payload: LoginRequest): Promise<LoginResponse> {
    return request({ url: '/auth/login', method: 'post', data: payload })
  },
  me(): Promise<AuthUser> {
    return request({ url: '/auth/me', method: 'get' })
  },
  logout(): Promise<void> {
    return request({ url: '/auth/logout', method: 'post' })
  }
}