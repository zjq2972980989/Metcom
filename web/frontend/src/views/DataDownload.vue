<script setup>
import { computed, onMounted, ref } from 'vue'

import { endpoints } from '../api/client'
import { submitDownload } from '../api/online'
import { useApi } from '../composables/useApi'

// 数据源、参数等选项全部来自后端 /api/meta，不在前端写死；
// 后端按"真实可运行"标记数据源能力，不可用的直接禁用，避免点了才报错。
const meta = useApi(endpoints.meta)

const dataset = ref('')
const startTime = ref('')
const endTime = ref('')
const submitting = ref(false)
const error = ref('')
const submitted = ref(null)

const sources = computed(() => meta.data.value?.sources ?? [])
const selected = computed(() => sources.value.find((item) => item.key === dataset.value) || null)

// 下载入口只列出核心引擎已实现下载器的数据源（ERA5 / MERRA-2）。
// CRA40 / CRA V1.5 / CMA-MESO 暂无下载器，既不出现在下拉里，也不伪造下载能力。
const downloadSources = computed(
  () => sources.value.filter((item) => item.downloader_available),
)

// MERRA-2 的展示信息：仅用于说明数据源本身，能否下载始终由后端 /api/meta 的
// download_available 决定（代码有下载器 ≠ 当前机器已配置 Earthdata 凭据）。
const MERRA2_DETAILS = [
  '数据类型：压力层三维分析资料（inst6_3d_ana_Np）',
  '时间分辨率：6h（00 / 06 / 12 / 18 UTC）',
  '空间分辨率：0.5° × 0.625°',
  '气压层：42 levels',
  '数据源：NASA GES DISC',
]
const MERRA2_NO_CREDENTIAL =
  'MERRA-2 下载需要配置 NASA Earthdata 账号凭据，当前机器尚未配置。'

// 提交前必须确认当前数据源真的可下载，不只是"代码支持"
const canSubmit = computed(
  () =>
    Boolean(selected.value?.download_available) &&
    Boolean(dataset.value) &&
    Boolean(startTime.value) &&
    Boolean(endTime.value) &&
    !submitting.value,
)

function sourceLabel(source) {
  return source.download_available ? source.name : `${source.name}（暂不可用）`
}

function sourceHint(source) {
  if (!source) return ''
  if (source.key === 'merra2') {
    return source.download_available
      ? '下载官方 inst6_3d_ana_Np 日文件（一天一文件，含四个分析时次）。'
      : MERRA2_NO_CREDENTIAL
  }
  if (source.download_available) {
    return `时间分辨率 ${source.time_resolution}，空间分辨率 ${source.spatial_resolution}。`
  }
  return source.note ? `暂不可用：${source.note}。` : '暂不可用。'
}

function sourceDetails(source) {
  return source && source.key === 'merra2' ? MERRA2_DETAILS : []
}

// datetime-local 的值是本地墙钟时间，按原样提交给后端（不做时区换算），
// 这样用户选择的时间与数据文件里的时次一一对应。
function toIso(value) {
  return value.length === 16 ? `${value}:00` : value
}

onMounted(async () => {
  await meta.load()
  const usable = downloadSources.value.find((item) => item.download_available)
  dataset.value = usable?.key ?? downloadSources.value[0]?.key ?? ''
})

async function onSubmit() {
  if (!canSubmit.value) return
  if (toIso(startTime.value) > toIso(endTime.value)) {
    error.value = '开始时间不能晚于结束时间。'
    return
  }

  submitting.value = true
  error.value = ''
  submitted.value = null
  try {
    submitted.value = await submitDownload({
      dataset: dataset.value,
      startTime: toIso(startTime.value),
      endTime: toIso(endTime.value),
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
    <h2 class="card-title">数据下载</h2>
    <p class="muted task-create-note">
      选择数据源与时间范围，后台调用 MetCom 下载器获取再分析资料。接口地址、文件目录与文件命名全部由后台处理。
    </p>

    <form class="online-form" @submit.prevent="onSubmit">
      <div class="online-row">
        <label class="field">
          <span class="field-label">数据源</span>
          <select v-model="dataset" class="input">
            <option
              v-for="source in downloadSources"
              :key="source.key"
              :value="source.key"
            >
              {{ sourceLabel(source) }}
            </option>
          </select>
          <span class="field-note">{{ sourceHint(selected) }}</span>
          <span v-for="line in sourceDetails(selected)" :key="line" class="field-note">
            {{ line }}
          </span>
        </label>

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
          <span class="field-note">按数据源自身的时间分辨率取数，无需填写步长与文件格式。</span>
        </div>
      </div>

      <p v-if="error" class="notice notice-error">{{ error }}</p>
      <p v-else-if="submitted" class="notice notice-ok">
        任务已提交：{{ submitted.name }}（{{ submitted.status }}）。
        <RouterLink :to="`/preview/${submitted.task_id}`">查看结果</RouterLink>
        <RouterLink to="/tasks">前往任务中心</RouterLink>
      </p>

      <div class="online-actions">
        <button class="btn btn-primary" type="submit" :disabled="!canSubmit">
          {{ submitting ? '提交中…' : '开始下载' }}
        </button>
        <span class="field-note">提交后立即返回，不在此页等待；任务进度请在任务中心查看。</span>
      </div>
    </form>
  </div>
</template>
