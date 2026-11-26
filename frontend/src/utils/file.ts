export const MAX_FILE_BYTES = 50 * 1024 * 1024
export const ALLOWED_EXTENSIONS = ['pdf', 'docx', 'md']

export function extensionOf(name: string): string {
  const idx = name.lastIndexOf('.')
  return idx >= 0 ? name.slice(idx + 1).toLowerCase() : ''
}

export function isSupportedFile(file: File): boolean {
  return ALLOWED_EXTENSIONS.includes(extensionOf(file.name))
}

export function localFileError(file: File): string | null {
  if (!file.name.trim()) return '文件名不能为空'
  if (!isSupportedFile(file)) return '仅支持 PDF、Word(.docx)、Markdown'
  if (file.size <= 0) return '文件为空'
  if (file.size > MAX_FILE_BYTES) return '单个文件不能超过 50MB'
  return null
}

export function makeClientId(): string {
  const cryptoObj = window.crypto
  if (cryptoObj && 'randomUUID' in cryptoObj) return cryptoObj.randomUUID()
  return `f_${Date.now()}_${Math.random().toString(16).slice(2)}`
}

export async function sha256OfFile(file: File): Promise<string | undefined> {
  if (!window.crypto?.subtle || !file.arrayBuffer) return undefined
  try {
    const buffer = await file.arrayBuffer()
    const digest = await window.crypto.subtle.digest('SHA-256', buffer)
    return Array.from(new Uint8Array(digest))
      .map((byte) => byte.toString(16).padStart(2, '0'))
      .join('')
  } catch {
    return undefined
  }
}