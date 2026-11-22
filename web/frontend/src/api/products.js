// 标准产品接口封装：StandardProducts.vue 只调用这里，不直接写 fetch。
// 后端契约见 web/backend/routers/products.py。
//
// 数据说明：后端把"演示数据"（web/data/products/demo）与正式产品
// （web/data/products/standard）分开标注 is_demo，页面必须据此提示，
// 绝不能把演示数据当正式产品展示。
import { api, apiUrl } from './client'

/** 产品仓库里实际可用的数据源 / 区域 / 分辨率 / 日期 / 时次（只列真实存在的数据）。 */
export function getProductSources({ includeDemo = true } = {}) {
  const query = new URLSearchParams({ include_demo: String(includeDemo) })
  return api.get(`/products/sources?${query.toString()}`)
}

/** 产品目录（GET /api/products），可按数据源 / 区域 / 分辨率 / 日期过滤。 */
export function getProducts({ source, region, resolution, date, includeDemo = true } = {}) {
  const query = new URLSearchParams({ include_demo: String(includeDemo) })
  if (source) query.set('source', source)
  if (region) query.set('region', region)
  if (resolution) query.set('resolution', resolution)
  if (date) query.set('date', date)
  return api.get(`/products?${query.toString()}`)
}

/** 单个产品元信息（GET /api/products/{source}/{region}/{resolution}/{date}）。 */
export function getProductDetail({ source, region, resolution, date, includeDemo = true } = {}) {
  const query = new URLSearchParams({ include_demo: String(includeDemo) })
  return api.get(
    `/products/${encodeURIComponent(source)}/${encodeURIComponent(region)}`
    + `/${encodeURIComponent(resolution)}/${encodeURIComponent(date)}?${query.toString()}`,
  )
}

/** 产品数据预览（GET .../{hour}/data），limit=0 表示返回全部行。 */
export function getProductData({ source, region, resolution, date, hour, limit = 200, includeDemo = true } = {}) {
  const query = new URLSearchParams({
    limit: String(limit),
    include_demo: String(includeDemo),
  })
  return api.get(
    `/products/${encodeURIComponent(source)}/${encodeURIComponent(region)}`
    + `/${encodeURIComponent(resolution)}/${encodeURIComponent(date)}`
    + `/${encodeURIComponent(String(hour))}/data?${query.toString()}`,
  )
}

/** 产品文件下载地址（GET .../{hour}/download），供 <a href> 直接使用。 */
export function productDownloadUrl({ source, region, resolution, date, hour, includeDemo = true }) {
  const query = new URLSearchParams({ include_demo: String(includeDemo) })
  return apiUrl(
    `/products/${encodeURIComponent(source)}/${encodeURIComponent(region)}`
    + `/${encodeURIComponent(resolution)}/${encodeURIComponent(date)}`
    + `/${encodeURIComponent(String(hour))}/download?${query.toString()}`,
  )
}

/** 日期级产品 ZIP 地址（GET .../{date}/archive），包含该日期全部时次。 */
export function productArchiveUrl({ source, region, resolution, date, includeDemo = true }) {
  const query = new URLSearchParams({ include_demo: String(includeDemo) })
  return apiUrl(
    `/products/${encodeURIComponent(source)}/${encodeURIComponent(region)}`
    + `/${encodeURIComponent(resolution)}/${encodeURIComponent(date)}`
    + `/archive?${query.toString()}`,
  )
}
