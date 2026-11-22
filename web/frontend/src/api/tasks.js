// 任务中心接口封装：TaskCenter.vue 只调用这里，不直接写 fetch。
// 后端契约见 web/backend/routers/tasks.py。
import { api, apiUrl } from './client'

/** 创建任务（POST /api/tasks），返回 { id, status }。 */
export function createTask(payload) {
  return api.post('/tasks', payload)
}

/** 任务列表（GET /api/tasks），返回 { items, total, page, page_size, pages }。 */
export function getTasks({ page = 1, pageSize = 20, type } = {}) {
  const query = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  if (type) query.set('type', type)
  return api.get(`/tasks?${query.toString()}`)
}

/** 单个任务详情 / 状态（GET /api/tasks/{id}）。 */
export function getTask(taskId) {
  return api.get(`/tasks/${taskId}`)
}

/** 任务结果（GET /api/tasks/{id}/result）。 */
export function getTaskResult(taskId) {
  return api.get(`/tasks/${taskId}/result`)
}

/** 结果下载地址（GET /api/tasks/{id}/download），供浏览器直接打开或 <a> 引用。 */
export function downloadResultUrl(taskId) {
  return apiUrl(`/tasks/${taskId}/download`)
}
