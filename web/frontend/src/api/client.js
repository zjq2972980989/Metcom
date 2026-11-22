// 后端接口客户端：唯一出口，页面里不直接写 fetch。
// 开发期 /api 由 Vite 代理到 127.0.0.1:8000，所以这里只需要相对路径。
const BASE = import.meta.env.VITE_API_BASE || '/api'

// 网关类状态码：代表请求没到达后端本身（开发代理在后端未启动时返回）
const GATEWAY_STATUS = new Set([502, 503, 504])

export const API_BASE = BASE

export class ApiError extends Error {
  constructor(message, status = 0, payload = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.payload = payload
  }
}

/** 后端错误体统一为 { detail }；detail 也可能是 FastAPI 的校验数组，这里统一成一句话。 */
function errorMessage(payload, status) {
  const detail = payload && typeof payload === 'object' ? payload.detail : null
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (item && typeof item === 'object' ? item.msg : String(item)))
      .filter(Boolean)
    if (messages.length) return messages.join('；')
  }
  if (typeof detail === 'string' && detail.trim()) return detail
  return `请求失败（HTTP ${status}）`
}

async function request(path, { method = 'GET', body, signal } = {}) {
  let response
  try {
    response = await fetch(`${BASE}${path}`, {
      method,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    })
  } catch (cause) {
    throw new ApiError('无法连接后端服务，请确认 FastAPI 已在 127.0.0.1:8000 启动。', 0, null)
  }

  const text = await response.text()
  let payload = null
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      payload = text
    }
  }

  if (!response.ok) {
    // 502 / 503 / 504 是开发代理（Vite）在后端未启动时返回的网关错误：
    // 此时后端实际上没有连上，按「未连接」处理，
    // 避免首页把它显示成红色「API 异常」。
    if (GATEWAY_STATUS.has(response.status)) {
      throw new ApiError('无法连接后端服务，请确认 FastAPI 已在 127.0.0.1:8000 启动。', 0, payload)
    }

    throw new ApiError(errorMessage(payload, response.status), response.status, payload)
  }

  return payload
}

export const api = {
  get: (path, options) => request(path, options),
  post: (path, body, options) => request(path, { ...options, method: 'POST', body }),
}

/** 相对接口地址 → 浏览器可直接访问的 URL（结果下载这类链接用得到）。 */
export function apiUrl(path) {
  return `${BASE}${path}`
}

// 常用接口（阶段一只用到前三个中的 health/meta/engine）
export const endpoints = {
  health: () => api.get('/health'),
  meta: () => api.get('/meta'),
  engine: () => api.get('/engine'),
}
