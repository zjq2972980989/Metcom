// 在线计算接口封装：数据下载与参数计算。
// 两个接口都只负责创建任务并立即返回 task_id，真正的执行在后台由任务中心完成。
// 后端契约见 web/backend/routers/download.py 与 routers/calculation.py。
import { api } from './client'

/** 提交数据下载任务（POST /api/download）。 */
export function submitDownload({ dataset, startTime, endTime }) {
  return api.post('/download', {
    dataset,
    start_time: startTime,
    end_time: endTime,
  })
}

/**
 * 提交参数计算任务（POST /api/calculation）。
 *
 * sites 省略（undefined / null / 空数组）时不发送该字段，后端沿用平台默认站点文件，
 * 与历史请求完全一致；传入非空数组时由后端为该任务生成专属站点文件。
 *
 * outputFormat 省略（undefined / 空串）时不发送该字段，后端使用平台默认格式（txt）；
 * 可选值与后端 /api/meta 支持的格式一致（txt / dat / mat）。
 */
export function submitCalculation({ dataset, parameters, startTime, endTime, sites, outputFormat }) {
  const payload = {
    dataset,
    parameters,
    start_time: startTime,
    end_time: endTime,
  }
  if (Array.isArray(sites) && sites.length > 0) {
    payload.sites = sites
  }
  if (typeof outputFormat === 'string' && outputFormat) {
    payload.output_format = outputFormat
  }
  return api.post('/calculation', payload)
}

/**
 * 提交定制产品（格网模式）任务（POST /api/custom）。
 *
 * 后端契约见 web/backend/routers/custom.py 与 models/schemas.py 的 CustomRequest：
 *
 *   - 目前只实现 GRID 模式，mode 统一发大写 "GRID"；
 *   - 四个空间范围字段必须是 JSON number，因此这里统一用 Number() 转换，
 *     绝不发送 "115" 这类字符串；
 *   - 空间分辨率由后端按数据源自动匹配（ERA5 0.25° × 0.25°），前端不发送；
 *   - 格点数量只是页面提示，不作为参数发送，后端 validate_grid 仍是最终校验。
 */
export function submitCustomGrid({
  dataset,
  parameters,
  startTime,
  endTime,
  lonMin,
  lonMax,
  latMin,
  latMax,
}) {
  return api.post('/custom', {
    mode: 'GRID',
    dataset,
    parameters,
    start_time: startTime,
    end_time: endTime,
    lon_min: Number(lonMin),
    lon_max: Number(lonMax),
    lat_min: Number(latMin),
    lat_max: Number(latMax),
  })
}

/**
 * 可用于时间插值的输入任务（GET /api/timeinterp/inputs）。
 *
 * 返回平台已完成、可作为插值输入的反演任务，以及每个任务真实的时次、
 * 站点数、参数列与"不可插值"原因；前端不猜能力，只显示后端结论。
 */
export function getTimeInterpInputs() {
  return api.get('/timeinterp/inputs')
}

/**
 * 提交时间插值任务（POST /api/timeinterp）。
 *
 * 后端契约见 web/backend/routers/timeinterp.py 与 models/schemas.py 的
 * TimeInterpRequest：输入只能是平台已完成的参数计算任务（task_id），
 * 不发送任何文件路径；targetInterval 单位是小时，可选值由后端
 * /api/timeinterp/inputs 的 target_intervals 给出。
 *
 * 不发送 parameters 字段：原生插值的输出表头固定为 4 列站点信息 + 8 个参数，
 * 只有 8 个参数齐全时列宽才对得上，因此后端固定插值全部 8 个参数。
 */
export function submitTimeInterp({ sourceTaskId, targetInterval }) {
  return api.post('/timeinterp', {
    source_task_id: sourceTaskId,
    target_interval: Number(targetInterval),
  })
}
