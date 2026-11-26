import { onBeforeUnmount, ref } from 'vue'

export function usePolling(task: () => Promise<void>, intervalMs = 2000) {
  const active = ref(false)
  let timer: number | null = null
  let running = false

  async function tick(): Promise<void> {
    if (!active.value || running) return
    if (document.visibilityState === 'hidden') {
      schedule()
      return
    }
    running = true
    try {
      await task()
    } catch {
      // 轮询失败由调用方决定是否提示；这里只保持节奏
    } finally {
      running = false
      if (active.value) schedule()
    }
  }

  function schedule(): void {
    if (!active.value) return
    if (timer != null) window.clearTimeout(timer)
    timer = window.setTimeout(tick, intervalMs)
  }

  function start(): void {
    if (active.value) return
    active.value = true
    void tick()
  }

  function stop(): void {
    active.value = false
    if (timer != null) {
      window.clearTimeout(timer)
      timer = null
    }
  }

  function onVisible(): void {
    if (active.value && document.visibilityState === 'visible') {
      if (timer != null) window.clearTimeout(timer)
      timer = null
      void tick()
    }
  }

  document.addEventListener('visibilitychange', onVisible)

  onBeforeUnmount(() => {
    document.removeEventListener('visibilitychange', onVisible)
    stop()
  })

  return { active, start, stop }
}