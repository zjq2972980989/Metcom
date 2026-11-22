<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { endpoints } from '../api/client'
import { submitCustomGrid } from '../api/online'
import { useApi } from '../composables/useApi'

// 数据源与参数清单来自后端 /api/meta，前端不另写一份枚举。
const meta = useApi(endpoints.meta)
const router = useRouter()

// 当前 Grid 只支持 ERA5；MERRA-2 在下拉里保留但不可提交。
const GRID_DATASET = 'era5'
const GRID_SOURCE_KEYS = ['era5', 'merra2']

// 当前 Grid DEM 覆盖范围，仅用于界面提示与提交前的可读性检查；
// 最终判定仍然后端负责（超出范围时后端返回 422 + 明确说明）。
const DEM_BOUNDS = { lonMin: 113, lonMax: 120, latMin: 36, latMax: 43 }
// 推荐默认区域：ERA5 下为 3 × 3 = 9 个格点，不会触发大规模计算。
const DEFAULT_REGION = { lonMin: 115, lonMax: 115.5, latMin: 37, latMax: 37.5 }
const DEFAULT_TIME = '2025-06-01T00:00'
// 与后端 validation.MAX_GRID_POINTS 一致，仅用于提前提示。
const MAX_GRID_POINTS = 10000
// 提交成功先显示提示，再进入任务中心（任务在后台执行，本页不轮询）。
const REDIRECT_DELAY_MS = 600

const dataset = ref(GRID_DATASET)
const picked = ref([])
const startTime = ref(DEFAULT_TIME)
const endTime = ref(DEFAULT_TIME)
const lonMin = ref(DEFAULT_REGION.lonMin)
const lonMax = ref(DEFAULT_REGION.lonMax)
const latMin = ref(DEFAULT_REGION.latMin)
const latMax = ref(DEFAULT_REGION.latMax)

const submitting = ref(false)
const error = ref('')
const submitted = ref(null)
let redirectTimer = null

const sources = computed(() => meta.data.value?.sources ?? [])
const parameters = computed(() => meta.data.value?.parameters ?? [])
const selected = computed(() => sources.value.find((item) => item.key === dataset.value) || null)
const gridSupported = computed(() => dataset.value === GRID_DATASET)

// 下拉里只保留 ERA5 与 MERRA-2；/api/meta 拿不到时退回最小可用的静态描述。
const sourceOptions = computed(() => {
  const allowed = sources.value.filter((item) => GRID_SOURCE_KEYS.includes(item.key))
  if (allowed.length) return allowed
  return [
    { key: 'era5', name: 'ERA5', spatial_resolution: '0.25° × 0.25°' },
    { key: 'merra2', name: 'MERRA-2', spatial_resolution: '0.5° × 0.625°' },
  ]
})

// 原生分辨率（lat, lon）只用于格点数预览；真实分辨率由后端按数据源决定。
const gridStep = computed(() => {
  const numbers = String(selected.value?.spatial_resolution ?? '').match(/\d+(?:\.\d+)?/g)
  if (!numbers || numbers.length < 2) return null
  const lat = Number(numbers[0])
  const lon = Number(numbers[1])
  if (!(lat > 0) || !(lon > 0)) return null
  return { lat, lon }
})

function axisCount(min, max, size) {
  const lo = Number(min)
  const hi = Number(max)
  if (!Number.isFinite(lo) || !Number.isFinite(hi) || !(size > 0) || hi < lo) return 0
  return Math.round((hi - lo) / size) + 1      // 与后端 grid_service._colon 同语义
}

const pointPreview = computed(() => {
  if (!gridSupported.value || !gridStep.value) return null
  const nLon = axisCount(lonMin.value, lonMax.value, gridStep.value.lon)
  const nLat = axisCount(latMin.value, latMax.value, gridStep.value.lat)
  if (nLon < 1 || nLat < 1) return null
  return { nLon, nLat, total: nLon * nLat }
})

/** 提交前的可读性检查（用户体验层；后端 validate_grid 仍是最终校验）。 */
const rangeError = computed(() => {
  const fields = [
    ['最小经度', lonMin.value],
    ['最大经度', lonMax.value],
    ['最小纬度', latMin.value],
    ['最大纬度', latMax.value],
  ]
  for (const [label, raw] of fields) {
    if (raw === '' || raw === null || raw === undefined) return `${label}不能为空。`
    if (!Number.isFinite(Number(raw))) return `${label}必须是数字。`
  }
  const lo = Number(lonMin.value)
  const hi = Number(lonMax.value)
  const la = Number(latMin.value)
  const ha = Number(latMax.value)
  if (lo < -180 || lo > 180 || hi < -180 || hi > 180) return '经度需在 -180 ~ 180 之间。'
  if (la < -90 || la > 90 || ha < -90 || ha > 90) return '纬度需在 -90 ~ 90 之间。'
  if (lo >= hi) return '最小经度必须小于最大经度。'
  if (la >= ha) return '最小纬度必须小于最大纬度。'
  if (lo < DEM_BOUNDS.lonMin || hi > DEM_BOUNDS.lonMax
    || la < DEM_BOUNDS.latMin || ha > DEM_BOUNDS.latMax) {
    return `当前定制区域超出可用 DEM 范围：${DEM_BOUNDS.lonMin}°E–${DEM_BOUNDS.lonMax}°E / `
      + `${DEM_BOUNDS.latMin}°N–${DEM_BOUNDS.latMax}°N。`
  }
  return ''
})

const tooManyPoints = computed(() => Boolean(pointPreview.value)
  && pointPreview.value.total > MAX_GRID_POINTS)

const canSubmit = computed(() =>
  gridSupported.value
  && Boolean(dataset.value)
  && picked.value.length > 0
  && Boolean(startTime.value) && Boolean(endTime.value)
  && rangeError.value === ''
  && !tooManyPoints.value
  && !submitting.value)

function optionLabel(source) {
  if (source.key === GRID_DATASET) {
    return `${source.name} · 原生分辨率 ${source.spatial_resolution}`
  }
  return `${source.name}（暂不可用）`
}

function sourceHint(source) {
  if (!source) return ''
  if (source.key === GRID_DATASET) {
    return `时间分辨率 ${source.time_resolution}，空间分辨率 ${source.spatial_resolution}。`
  }
  return 'MERRA-2 格网定制暂未接入，当前只能提交 ERA5。'
}

// datetime-local 的值按墙钟时间原样提交，不做时区换算（与数据文件时次一一对应）。
function toIso(value) {
  return value.length === 16 ? `${value}:00` : value
}

/** 错误提示：422 优先显示后端 detail，500 收敛成一句话，不暴露任何堆栈。 */
function describeError(err) {
  const status = err?.status ?? 0
  const detail = String(err?.message ?? '').trim()
  if (status === 0) return detail || '任务提交失败，请稍后重试。'
  if (status >= 500) return '任务提交失败，请稍后重试。'
  if (status === 422 && /DEM/.test(detail)) {
    return `当前定制区域超出可用 DEM 范围：${DEM_BOUNDS.lonMin}°E–${DEM_BOUNDS.lonMax}°E / `
      + `${DEM_BOUNDS.latMin}°N–${DEM_BOUNDS.latMax}°N。${detail}`
  }
  return detail || '任务提交失败，请稍后重试。'
}

onMounted(async () => {
  await meta.load()
  picked.value = parameters.value.map((item) => item.key)   // 默认六个参数全选
  dataset.value = GRID_DATASET
})

onBeforeUnmount(() => clearTimeout(redirectTimer))

async function onSubmit() {
  if (!canSubmit.value) return
  if (rangeError.value) {
    error.value = rangeError.value
    return
  }
  if (toIso(startTime.value) > toIso(endTime.value)) {
    error.value = '开始时间不能晚于结束时间。'
    return
  }

  submitting.value = true
  error.value = ''
  submitted.value = null
  try {
    submitted.value = await submitCustomGrid({
      dataset: dataset.value,
      parameters: picked.value,
      startTime: toIso(startTime.value),
      endTime: toIso(endTime.value),
      lonMin: lonMin.value,
      lonMax: lonMax.value,
      latMin: latMin.value,
      latMax: latMax.value,
    })
    redirectTimer = setTimeout(() => router.push('/tasks'), REDIRECT_DELAY_MS)
  } catch (err) {
    error.value = describeError(err)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <section class="page">
    <header class="page-header">
      <h1>定制产品</h1>
      <p class="page-desc">
        根据数据源、时间范围和空间范围生成定制化对流层参数产品。提交后由后台完成格点生成与参数反演，本页不等待计算结果。
      </p>
    </header>

    <div class="card">
      <h2 class="card-title">Grid 区域定制</h2>
      <p class="muted task-create-note">
        空间分辨率随数据源自动匹配，格点高程由后台读取 DEM 自动生成，用户无需填写。
      </p>

      <form class="online-form" @submit.prevent="onSubmit">
        <label class="field">
          <span class="field-label">数据源</span>
          <select v-model="dataset" class="input">
            <option
              v-for="source in sourceOptions"
              :key="source.key"
              :value="source.key"
              :disabled="source.key !== 'era5'"
            >
              {{ optionLabel(source) }}
            </option>
          </select>
          <span class="field-note">{{ sourceHint(selected) }}</span>
        </label>

        <div class="field">
          <span class="field-label">输出参数</span>
          <div class="param-picks">
            <label v-for="item in parameters" :key="item.key" class="param-pick">
              <input v-model="picked" type="checkbox" :value="item.key" />
              <span class="param-pick-name">{{ item.name }}</span>
              <span class="param-pick-unit">{{ item.key }} · {{ item.unit }}</span>
            </label>
          </div>
          <span class="field-note">默认全部选中；取消参数后，结果文件只保留所选列。</span>
        </div>

        <div class="field">
          <span class="field-label">时间范围</span>
          <div class="online-row">
            <label class="field">
              <span class="field-label">开始时间</span>
              <input v-model="startTime" class="input" type="datetime-local" />
            </label>
            <label class="field">
              <span class="field-label">结束时间</span>
              <input v-model="endTime" class="input" type="datetime-local" />
            </label>
          </div>
          <span class="field-note">按所选范围内已获取的每个时次反演，起点与终点相同即为单个时刻。</span>
        </div>

        <div class="field">
          <span class="field-label">空间范围</span>
          <div class="online-row">
            <label class="field">
              <span class="field-label">经度最小值</span>
              <input v-model="lonMin" class="input" type="number" step="any" />
            </label>
            <label class="field">
              <span class="field-label">经度最大值</span>
              <input v-model="lonMax" class="input" type="number" step="any" />
            </label>
          </div>
          <div class="online-row">
            <label class="field">
              <span class="field-label">纬度最小值</span>
              <input v-model="latMin" class="input" type="number" step="any" />
            </label>
            <label class="field">
              <span class="field-label">纬度最大值</span>
              <input v-model="latMax" class="input" type="number" step="any" />
            </label>
          </div>
          <span class="field-note">
            当前 Grid DEM 覆盖范围：{{ DEM_BOUNDS.lonMin }}°E–{{ DEM_BOUNDS.lonMax }}°E /
            {{ DEM_BOUNDS.latMin }}°N–{{ DEM_BOUNDS.latMax }}°N，超出范围的区域无法生成格点高程。
          </span>
          <span class="field-note">
            Grid 分辨率由数据源自动匹配，用户无需设置。ERA5：0.25° × 0.25°。
          </span>
          <span v-if="pointPreview" class="field-note">
            预计格点：{{ pointPreview.nLon }} × {{ pointPreview.nLat }} =
            {{ pointPreview.total }} 个格点（仅供参考，最终以后端校验为准）。
          </span>
          <span v-if="tooManyPoints" class="field-note">
            预计格点超过单任务上限 {{ MAX_GRID_POINTS }} 个，请缩小空间范围。
          </span>
        </div>

        <p v-if="!gridSupported" class="notice notice-error">
          MERRA-2 格网定制暂未接入，请选择 ERA5。
        </p>
        <p v-if="error" class="notice notice-error">{{ error }}</p>
        <p v-else-if="submitted" class="notice notice-ok">
          任务已提交（{{ submitted.status }}），正在前往任务中心…
          <RouterLink to="/tasks">立即前往</RouterLink>
        </p>

        <div class="online-actions">
          <button class="btn btn-primary" type="submit" :disabled="!canSubmit">
            {{ submitting ? '提交中…' : '开始计算' }}
          </button>
          <span class="field-note">提交后立即返回，任务进度请在任务中心查看。</span>
        </div>
      </form>
    </div>
  </section>
</template>
