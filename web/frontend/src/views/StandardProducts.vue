<script setup>
import { computed, onMounted, ref, watch } from 'vue'

import { ApiError } from '../api/client'
import {
  getProductData,
  getProductDetail,
  getProductSources,
  getProducts,
  productArchiveUrl,
  productDownloadUrl,
} from '../api/products'

/** 预览行数选项：0 表示返回全部行（大文件谨慎使用）。 */
const LIMIT_OPTIONS = [
  { value: 100, label: '前 100 行' },
  { value: 200, label: '前 200 行' },
  { value: 500, label: '前 500 行' },
  { value: 0, label: '全部行' },
]

/** Header 里只在详情卡片里单独展示的字段，不在通用键值表里重复。 */
const HEADER_HIDDEN = new Set(['Version', 'Product_Type'])

const sources = ref([])
const catalog = ref([])
const catalogLoading = ref(false)
const catalogError = ref('')

const source = ref('')
const region = ref('')
const resolution = ref('')
const date = ref('')

const detail = ref(null)
const detailLoading = ref(false)
const detailError = ref('')
/** 查询条件指纹：同一组条件不重复请求（取消依赖项变化触发的重复加载）。 */
let appliedQuery = ''
/** 请求序号：并发响应只允许最新一次落到界面，避免旧响应覆盖新结果。 */
let detailSeq = 0
let previewSeq = 0

const hour = ref(null)
const limit = ref(200)
const preview = ref(null)
const previewLoading = ref(false)
const previewError = ref('')

const current = computed(() => sources.value.find((item) => item.source === source.value) || null)
const regions = computed(() => current.value?.regions ?? [])
const resolutions = computed(() => current.value?.resolutions ?? [])
/** 日期选项只包含"当前数据源 + 区域 + 分辨率"下真实存在的日期。 */
const dates = computed(() => {
  const filtered = catalog.value.filter(
    (item) => item.source === source.value
      && (!region.value || item.region === region.value)
      && (!resolution.value || item.resolution === resolution.value),
  )
  return [...new Set(filtered.map((item) => item.date))].sort()
})
const isDemo = computed(() =>
  Boolean(detail.value?.is_demo || current.value?.is_demo || preview.value?.is_demo),
)
const hasData = computed(() => catalog.value.length > 0)

const headerRows = computed(() => {
  const meta = preview.value?.metadata ?? {}
  return Object.entries(meta)
    .filter(([key]) => !HEADER_HIDDEN.has(key))
    .map(([key, value]) => ({ key, value }))
})

/** 下载地址：hour 为 0（H00）同样必须可用，因此用 === null 判断。 */
const downloadHref = computed(() => {
  if (hour.value === null || !source.value) return ''
  return productDownloadUrl({
    source: source.value,
    region: region.value,
    resolution: resolution.value,
    date: date.value,
    hour: hour.value,
  })
})

const canQuery = computed(() =>
  Boolean(source.value && region.value && resolution.value && date.value))

/** 下载文件名：优先用产品真实文件名，未预览时按日期/时次拼一个可读名。 */
const downloadName = computed(() =>
  preview.value?.filename
  || (hour.value === null ? '' : `MetCom_${source.value}_${region.value}_${resolution.value}_${date.value}.H${String(hour.value).padStart(2, '0')}`))

/** 日期级打包下载：包含当前产品日期的全部时次，ZIP 内保留原目录结构。 */
const archiveHref = computed(() => {
  if (!canQuery.value) return ''
  return productArchiveUrl({
    source: source.value,
    region: region.value,
    resolution: resolution.value,
    date: date.value,
  })
})
const archiveName = computed(() =>
  canQuery.value ? `MetCom_${source.value}_${region.value}_${resolution.value}_${date.value}.zip` : '',
)

function message(err) {
  return err instanceof ApiError ? err.message : String(err)
}

function resetDetail() {
  detail.value = null
  detailError.value = ''
  hour.value = null
  preview.value = null
  previewError.value = ''
}

async function loadCatalog() {
  catalogLoading.value = true
  catalogError.value = ''
  try {
    const payload = await getProductSources()
    sources.value = payload?.sources ?? []
    const list = await getProducts()
    catalog.value = list?.products ?? []
    if (sources.value.length && !source.value) {
      source.value = sources.value[0].source
    }
    syncSelection()
  } catch (err) {
    catalogError.value = message(err)
    sources.value = []
    catalog.value = []
  } finally {
    catalogLoading.value = false
  }
}

/** 把区域 / 分辨率 / 日期收敛到当前数据源真实存在的取值（不造选项）。 */
function syncSelection() {
  if (!current.value) {
    region.value = ''
    resolution.value = ''
    date.value = ''
    return
  }
  if (!regions.value.includes(region.value)) region.value = regions.value[0] ?? ''
  if (!resolutions.value.includes(resolution.value)) resolution.value = resolutions.value[0] ?? ''
  if (!dates.value.includes(date.value)) date.value = dates.value[0] ?? ''
}

async function loadDetail() {
  resetDetail()
  if (!canQuery.value) return
  const seq = ++detailSeq
  detailLoading.value = true
  try {
    const payload = await getProductDetail({
      source: source.value,
      region: region.value,
      resolution: resolution.value,
      date: date.value,
    })
    if (seq !== detailSeq) return          // 已切换到其他查询条件，丢弃过期响应
    detail.value = payload
    if (payload?.hours?.length) hour.value = payload.hours[0]
  } catch (err) {
    if (seq !== detailSeq) return
    detailError.value = message(err)
  } finally {
    if (seq === detailSeq) detailLoading.value = false
  }
  if (seq === detailSeq && hour.value !== null) await loadPreview()
}

async function loadPreview() {
  if (hour.value === null || !canQuery.value) return
  const seq = ++previewSeq
  previewLoading.value = true
  previewError.value = ''
  try {
    const payload = await getProductData({
      source: source.value,
      region: region.value,
      resolution: resolution.value,
      date: date.value,
      hour: hour.value,
      limit: limit.value,
    })
    if (seq !== previewSeq) return          // 快速切时次/行数时丢弃过期响应
    preview.value = payload
  } catch (err) {
    if (seq !== previewSeq) return
    preview.value = null
    previewError.value = message(err)
  } finally {
    if (seq === previewSeq) previewLoading.value = false
  }
}

function pickHour(value) {
  if (value === hour.value) return
  hour.value = value
  loadPreview()
}

function hourLabel(value) {
  return `H${String(value).padStart(2, '0')}`
}

function formatCell(value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return String(value)
  if (Number.isInteger(value)) return String(value)
  return Math.abs(value) < 1000 ? value.toFixed(6) : String(value)
}

/** 只在查询条件真的变化时才请求，避免重复加载同一组条件。 */
function applyQuery() {
  const key = [source.value, region.value, resolution.value, date.value].join('|')
  if (key === appliedQuery) return
  appliedQuery = key
  loadDetail()
}

watch(source, () => {
  syncSelection()
  applyQuery()
})
watch([region, resolution, date], () => {
  syncSelection()
  applyQuery()
})
watch(limit, () => {
  if (hour.value !== null) loadPreview()
})

onMounted(async () => {
  await loadCatalog()
  applyQuery()
})
</script>

<template>
  <section class="page">
    <header class="page-header">
      <h1>标准产品</h1>
      <p class="page-desc">
        直接取用已经计算并标准化后的产品文件：查询 → 预览 Header 与数据 → 下载，无需等待计算任务。
      </p>
    </header>

    <p v-if="isDemo" class="notice notice-demo" data-testid="demo-notice">
      <strong>演示数据</strong>：当前展示的是交付方提供的 BTH 区域 0.125° 样例产品，
      仅用于验证查询 / 预览 / 下载链路，<strong>不是正式规格产品</strong>。
      正式产品（如 ERA5 / China / 1×1 / 6 h）在正式仓库就绪后会自动出现在这里。
    </p>

    <div class="card">
      <h2 class="card-title">查询条件</h2>
      <p v-if="catalogLoading" class="muted">正在读取产品仓库…</p>
      <p v-else-if="catalogError" class="notice notice-error">
        {{ catalogError }}
      </p>
      <template v-else-if="hasData">
        <div class="online-row std-filters">
          <label class="field">
            <span class="field-label">数据源</span>
            <select v-model="source" class="input">
              <option v-for="item in sources" :key="item.source" :value="item.source">
                {{ item.source }}（{{ item.time_resolution }}）
              </option>
            </select>
          </label>
          <label class="field">
            <span class="field-label">区域</span>
            <select v-model="region" class="input">
              <option v-for="item in regions" :key="item" :value="item">{{ item }}</option>
            </select>
          </label>
          <label class="field">
            <span class="field-label">空间分辨率</span>
            <select v-model="resolution" class="input">
              <option v-for="item in resolutions" :key="item" :value="item">{{ item }}</option>
            </select>
          </label>
          <label class="field">
            <span class="field-label">日期</span>
            <select v-model="date" class="input">
              <option v-for="item in dates" :key="item" :value="item">{{ item }}</option>
            </select>
          </label>
        </div>
        <p class="field-note">
          下拉选项全部来自后端实际存在的产品记录，没有数据的日期 / 时次不会出现。
          共 {{ catalog.length }} 条产品记录，{{ sources.length }} 个数据源。
        </p>
      </template>
      <p v-else class="notice notice-error">
        产品仓库为空：正式产品与演示数据目录下都没有可用的索引与产品文件。
        产品需先用 metcom_product 生成（web/data/products/standard 或 .../demo）。
      </p>
    </div>

    <div v-if="canQuery" class="card">
      <h2 class="card-title">产品详情</h2>
      <p v-if="detailLoading" class="muted">读取中…</p>
      <p v-else-if="detailError" class="notice notice-error">{{ detailError }}</p>
      <template v-else-if="detail">
        <dl class="kv">
          <dt>数据源</dt>
          <dd>{{ detail.source }}</dd>
          <dt>区域 / 分辨率</dt>
          <dd>{{ detail.region }} / {{ detail.resolution }}</dd>
          <dt>日期</dt>
          <dd>{{ detail.date }}</dd>
          <dt>时次数</dt>
          <dd>{{ detail.hours.length }} 个（{{ detail.hours.map(hourLabel).join('、') }}）</dd>
          <dt>数据来源</dt>
          <dd>
            <span v-if="detail.is_demo" class="std-badge">演示数据</span>
            <span v-else class="std-badge std-badge-real">正式产品</span>
          </dd>
        </dl>

        <div class="std-hours">
          <span class="field-label">时次</span>
          <div class="std-hour-picks">
            <button
              v-for="item in detail.hours"
              :key="item"
              type="button"
              class="std-hour"
              :class="{ 'is-active': item === hour }"
              @click="pickHour(item)"
            >
              {{ hourLabel(item) }}
            </button>
          </div>
        </div>

        <div class="std-preview-head">
          <span class="field-label">预览行数</span>
          <select v-model.number="limit" class="input std-limit">
            <option v-for="item in LIMIT_OPTIONS" :key="item.value" :value="item.value">
              {{ item.label }}
            </option>
          </select>
          <a
            v-if="downloadHref"
            class="btn btn-primary"
            :href="downloadHref"
            :download="downloadName"
          >
            下载 {{ preview ? preview.filename : hourLabel(hour) }} 文件
          </a>
          <a
            v-if="archiveHref"
            class="btn btn-outline"
            :href="archiveHref"
            :download="archiveName"
          >
            打包下载当日全部时次（ZIP）
          </a>
        </div>
      </template>
    </div>

    <div v-if="previewLoading" class="card">
      <p class="muted">正在读取产品数据…</p>
    </div>
    <div v-else-if="previewError" class="card">
      <p class="notice notice-error">{{ previewError }}</p>
    </div>
    <template v-else-if="preview">
      <div class="card">
        <h2 class="card-title">产品 Header</h2>
        <dl class="kv">
          <template v-for="row in headerRows" :key="row.key">
            <dt>{{ row.key }}</dt>
            <dd class="mono">{{ row.value }}</dd>
          </template>
        </dl>
        <p class="field-note">
          文件：<span class="mono">{{ preview.filename }}</span>
        </p>
      </div>

      <div class="card">
        <h2 class="card-title">
          数据预览
          <span class="std-count">
            已展示 {{ preview.returned_rows }} / 共 {{ preview.total_rows }} 行
          </span>
        </h2>
        <div class="std-table-wrap">
          <table class="std-table">
            <thead>
              <tr>
                <th v-for="col in preview.columns" :key="col" scope="col">
                  {{ col }}<span v-if="preview.units[col]" class="std-unit">[{{ preview.units[col] }}]</span>
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, index) in preview.data" :key="index">
                <td v-for="(cell, colIndex) in row" :key="colIndex" class="mono">
                  {{ formatCell(cell) }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="field-note">
          预览为只读展示；完整文件请用上方下载按钮获取（TXT/ASCII，含 Header + 数据区）。
        </p>
      </div>
    </template>
  </section>
</template>
