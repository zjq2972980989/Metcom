<script setup>
import { computed, onMounted, ref } from 'vue'

import { endpoints } from '../api/client'
import { submitCalculation } from '../api/online'
import { useApi } from '../composables/useApi'

// 数据源与输出参数都取自后端 /api/meta，前端不写死清单。
const meta = useApi(endpoints.meta)

// 结果文件格式：与后端真实支持的格式一致（定宽文本 / 结构体），
// 计算与格式转换全部由后台完成，前端只负责选择。
const OUTPUT_FORMATS = [
  { key: 'txt', label: 'TXT 文本', note: '定宽文本，首行表头 + 数据行，便于直接查看与二次处理。' },
  { key: 'dat', label: 'DAT 文本', note: '与 TXT 同构的定宽文本，沿用 MetCom 原有的 .dat 扩展名约定。' },
  { key: 'mat', label: 'MAT 结构体', note: '结构体文件，供 MATLAB / SciPy 直接读取。' },
]

const dataset = ref('')
const picked = ref([])
const outputFormat = ref('txt')
const timeMode = ref('single')
const singleTime = ref('')
const startTime = ref('')
const endTime = ref('')
const submitting = ref(false)
const error = ref('')
const submitted = ref(null)

// 测站：默认沿用平台站点文件；选择自定义时才在请求里携带 sites
const siteMode = ref('default')
const sites = ref([])

const sources = computed(() => meta.data.value?.sources ?? [])
const parameters = computed(() => meta.data.value?.parameters ?? [])
const selected = computed(() => sources.value.find((item) => item.key === dataset.value) || null)

// 后端返回的不可用诊断（CRA40 / CRA V1.5 / CMA-MESO 数据不完整时给出具体原因）。
// 可用时为空数组，前端不硬编码能力、也不绕过后端判定。
const selectedDiagnostics = computed(() => {
  if (!selected.value || selected.value.calculate_available) return []
  return selected.value.diagnostics ?? []
})

const formatNote = computed(
  () => OUTPUT_FORMATS.find((item) => item.key === outputFormat.value)?.note ?? '',
)

const hasTime = computed(() =>
  timeMode.value === 'single'
    ? Boolean(singleTime.value)
    : Boolean(startTime.value) && Boolean(endTime.value),
)

const canSubmit = computed(
  () =>
    Boolean(dataset.value) &&
    picked.value.length > 0 &&
    hasTime.value &&
    !submitting.value &&
    sitesReady.value,
)

/** 单个测站的校验提示；返回空串表示合法（与后端 validation.validate_sites 对齐）。 */
function siteError(site) {
  if (!String(site.name ?? '').trim()) return '站点名称不能为空。'
  if (/\s/.test(String(site.name))) return '站点名称不能包含空格。'
  for (const [key, label] of [['lat', '纬度'], ['lon', '经度'], ['height', '高程']]) {
    const raw = site[key]
    if (raw === '' || raw === null || raw === undefined) return `${label}不能为空。`
    const value = Number(raw)
    if (!Number.isFinite(value)) return `${label}必须是数字。`
    if (key === 'lat' && (value < -90 || value > 90)) return '纬度需在 -90 ~ 90 之间。'
    if (key === 'lon' && (value < -180 || value > 180)) return '经度需在 -180 ~ 180 之间。'
  }
  return ''
}

const sitesReady = computed(() => {
  if (siteMode.value !== 'custom') return true
  if (sites.value.length === 0) return false
  return sites.value.every((site) => siteError(site) === '')
})

function addSite() {
  sites.value.push({ name: '', lat: '', lon: '', height: '' })
}

function removeSite(index) {
  sites.value.splice(index, 1)
}

function sourceLabel(source) {
  return source.calculate_available ? source.name : `${source.name}（暂不可用）`
}

function sourceHint(source) {
  if (!source) return ''
  if (source.calculate_available) {
    return `时间分辨率 ${source.time_resolution}，空间分辨率 ${source.spatial_resolution}，共 ${source.levels} 层。`
  }
  return source.note ? `暂不可用：${source.note}。` : '暂不可用。'
}

function toIso(value) {
  return value.length === 16 ? `${value}:00` : value
}

onMounted(async () => {
  await meta.load()
  picked.value = parameters.value.map((item) => item.key)      // 默认全部选中
  const usable = sources.value.find((item) => item.calculate_available)
  dataset.value = usable?.key ?? sources.value[0]?.key ?? ''
})

async function onSubmit() {
  if (!canSubmit.value) return

  const start = timeMode.value === 'single' ? singleTime.value : startTime.value
  const end = timeMode.value === 'single' ? singleTime.value : endTime.value
  if (toIso(start) > toIso(end)) {
    error.value = '开始时间不能晚于结束时间。'
    return
  }

  // 自定义测站：前端先做一次可读性校验，后端仍会独立校验
  let payloadSites
  if (siteMode.value === 'custom') {
    if (sites.value.length === 0) {
      error.value = '请至少添加一个测站，或改用默认站点。'
      return
    }
    const bad = sites.value.find((site) => siteError(site) !== '')
    if (bad) {
      error.value = siteError(bad)
      return
    }
    payloadSites = sites.value.map((site) => ({
      name: String(site.name).trim(),
      lat: Number(site.lat),
      lon: Number(site.lon),
      height: Number(site.height),
    }))
  }

  submitting.value = true
  error.value = ''
  submitted.value = null
  try {
    submitted.value = await submitCalculation({
      dataset: dataset.value,
      parameters: picked.value,
      startTime: toIso(start),
      endTime: toIso(end),
      sites: payloadSites,
      outputFormat: outputFormat.value,
    })
  } catch (err) {
    error.value = err?.message || '任务提交失败，请稍后重试。'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="card">
    <h2 class="card-title">参数计算</h2>
    <p class="muted task-create-note">
      在已获取的再分析资料上反演对流层参数。数据读取、站点定位与反演全部由现有 Python MetCom 引擎完成。
    </p>

    <form class="online-form" @submit.prevent="onSubmit">
      <label class="field">
        <span class="field-label">数据源</span>
        <select v-model="dataset" class="input">
          <option
            v-for="source in sources"
            :key="source.key"
            :value="source.key"
            :disabled="!source.calculate_available"
          >
            {{ sourceLabel(source) }}
          </option>
        </select>
        <span class="field-note">{{ sourceHint(selected) }}</span>
        <span
          v-for="line in selectedDiagnostics"
          :key="line"
          class="field-note"
        >诊断：{{ line }}</span>
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
        <span class="field-label">结果文件格式</span>
        <div class="time-modes">
          <label v-for="item in OUTPUT_FORMATS" :key="item.key" class="time-mode">
            <input v-model="outputFormat" type="radio" :value="item.key" />{{ item.label }}
          </label>
        </div>
        <span class="field-note">{{ formatNote }}</span>
      </div>

      <div class="field">
        <span class="field-label">测站</span>
        <div class="time-modes">
          <label class="time-mode">
            <input v-model="siteMode" type="radio" value="default" />默认站点
          </label>
          <label class="time-mode">
            <input v-model="siteMode" type="radio" value="custom" />自定义测站
          </label>
        </div>

        <template v-if="siteMode === 'custom'">
          <div v-for="(site, index) in sites" :key="index" class="site-row">
            <input v-model="site.name" class="input" type="text" placeholder="站名" />
            <input v-model="site.lat" class="input" type="number" step="any" placeholder="纬度" />
            <input v-model="site.lon" class="input" type="number" step="any" placeholder="经度" />
            <input v-model="site.height" class="input" type="number" step="any" placeholder="正高(m)" />
            <button class="btn" type="button" @click="removeSite(index)">删除</button>
          </div>
          <div class="online-actions">
            <button class="btn" type="button" @click="addSite">添加测站</button>
            <span class="field-note">
              至少 1 个测站；纬度 -90 ~ 90，经度 -180 ~ 180，高程为正高（m）。
              自定义测站仅用于本次任务，不改动平台默认站点文件。
            </span>
          </div>
        </template>
        <span v-else class="field-note">使用平台默认站点文件（30 个站点）。</span>
      </div>

      <div class="field">
        <span class="field-label">时间</span>
        <div class="time-modes">
          <label class="time-mode">
            <input v-model="timeMode" type="radio" value="single" />单时刻
          </label>
          <label class="time-mode">
            <input v-model="timeMode" type="radio" value="range" />时间范围
          </label>
        </div>

        <div v-if="timeMode === 'single'" class="online-row online-time-body">
          <label class="field">
            <span class="field-label">日期时间</span>
            <input v-model="singleTime" class="input" type="datetime-local" />
          </label>
        </div>
        <div v-else class="online-row online-time-body">
          <label class="field">
            <span class="field-label">开始时间</span>
            <input v-model="startTime" class="input" type="datetime-local" />
          </label>
          <label class="field">
            <span class="field-label">结束时间</span>
            <input v-model="endTime" class="input" type="datetime-local" />
          </label>
        </div>
        <span class="field-note">按所选范围内已获取的每个时次反演，结果按年积日分目录输出。</span>
      </div>

      <p v-if="error" class="notice notice-error">{{ error }}</p>
      <p v-else-if="submitted" class="notice notice-ok">
        任务已提交：{{ submitted.name }}（{{ submitted.status }}）。
        <RouterLink :to="`/preview/${submitted.task_id}`">查看结果</RouterLink>
        <RouterLink to="/tasks">前往任务中心</RouterLink>
      </p>

      <div class="online-actions">
        <button class="btn btn-primary" type="submit" :disabled="!canSubmit">
          {{ submitting ? '提交中…' : '开始计算' }}
        </button>
        <span class="field-note">提交后立即返回，不在此页等待；任务进度请在任务中心查看。</span>
      </div>
    </form>
  </div>
</template>

<style scoped>
.site-row {
  display: grid;
  grid-template-columns: 1.2fr 1fr 1fr 1fr auto;
  gap: 10px;
  align-items: center;
  margin-bottom: 10px;
}

.site-row .btn { white-space: nowrap; }

@media (max-width: 720px) {
  .site-row { grid-template-columns: 1fr 1fr; }
}
</style>
