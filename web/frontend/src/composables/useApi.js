import { ref, shallowRef } from 'vue'

import { ApiError } from '../api/client'

/**
 * 统一的接口调用状态封装。
 *
 *   reachable  是否拿到了 HTTP 响应（false = 后端没起来 / 网络不通）
 *   httpOk     是否 2xx
 *   status     HTTP 状态码（0 表示没拿到响应）
 *   error      后端 detail 或连接错误信息
 *
 * 阶段的业务接口按设计返回 501 占位，因此 reachable 与 httpOk 需要分开判断。
 */
export function useApi(fetcher) {
  const data = shallowRef(null)
  const error = ref('')
  const status = ref(0)
  const reachable = ref(false)
  const httpOk = ref(false)
  const loading = ref(false)
  const loaded = ref(false)

  async function load() {
    loading.value = true
    loaded.value = false
    error.value = ''
    status.value = 0
    reachable.value = false
    httpOk.value = false

    try {
      const payload = await fetcher()
      data.value = payload ?? null
      status.value = 200
      reachable.value = true
      httpOk.value = true
    } catch (err) {
      data.value = null
      if (err instanceof ApiError) {
        error.value = err.message
        status.value = err.status
        reachable.value = err.status > 0
      } else {
        error.value = String(err)
      }
    } finally {
      loading.value = false
      loaded.value = true
    }
  }

  return { data, error, status, reachable, httpOk, loading, loaded, load }
}