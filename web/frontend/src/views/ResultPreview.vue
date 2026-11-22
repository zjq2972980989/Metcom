<script setup>
import { computed, onMounted, ref } from 'vue'

import { downloadResultUrl, getTask, getTaskResult } from '../api/tasks'

// 阶段三只做最小适配：任务信息 + 结果文件 + 下载入口。
// 地图、曲线、时间序列等可视化属于后续阶段。
const props = defineProps({
  taskId: { type: String, required: true },
})

const TYPE_LABELS = {
  DOWNLOAD: '数据下载',
  CALCULATION: '参数计算',
  CUSTOM: '定制产品',
  TIMEINTERP: '时间插值',
}
const STATUS_LABELS = { PENDING: '等待中', RUNNING: '处理中', SUCCESS: '已完成', FAILED: '失败' }
const RESULT_TYPE_LABELS = {
  file: '数据文件',
  grid: '格网结果',
  station: '站点结果',
  dataset: '数据集',
  timeinterp: '插值结果',
}

const task = ref(null)
const result = ref(null)
const error = ref('')
const loading = ref(true)

const payload = computed(() => result.value?.result || null)
const ready = computed(() => Boolean(result.value?.ready && payload.value))
const downloadHref = computed(() => downloadResultUrl(props.taskId))

function formatTime(iso) {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return String(iso)
  const pad = (value) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function formatValue(value) {
  if (Array.isArray(value)) return value.length ? value.join('、') : '—'
  if (value === null || value === undefined || value === '') return '—'
  return String(value)
}

function shortId(id) {
  return typeof id === 'string' ? id.slice(0, 8) : '—'
}

onMounted(async () => {
  try {
    task.value = await getTask(props.taskId)
    result.value = await getTaskResult(props.taskId)
  } catch (err) {
    error.value = err?.message || '结果读取失败，请稍后重试。'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <section class="page">
    <header class="page-header">
      <h1>结果预览</h1>
      <p class="page-desc">任务 {{ taskId }} 的结果信息与下载入口。</p>
    </header>

    <p v-if="loading" class="muted">读取中…</p>
    <p v-else-if="error" class="notice notice-error">{{ error }}</p>

    <template v-else-if="task">
      <div class="card">
        <h2 class="card-title">任务信息</h2>
        <dl class="kv drawer-kv">
          <dt>任务名称</dt>
          <dd>{{ task.name }}</dd>
          <dt>任务类型</dt>
          <dd>{{ TYPE_LABELS[task.type] || task.type }}</dd>
          <dt>状态</dt>
          <dd>
            <span class="status" :class="`is-${String(task.status).toLowerCase()}`">
              <span class="status-dot" aria-hidden="true"></span>{{ STATUS_LABELS[task.status] || task.status }}
            </span>
          </dd>
          <dt>数据源</dt>
          <dd>{{ formatValue(task.params?.dataset) }}</dd>
          <dt>输出参数</dt>
          <dd>{{ formatValue(task.params?.parameters) }}</dd>
          <dt>开始时间</dt>
          <dd>{{ formatValue(task.params?.start_time) }}</dd>
          <dt>结束时间</dt>
          <dd>{{ formatValue(task.params?.end_time) }}</dd>
          <template v-if="task.type === 'TIMEINTERP'">
            <dt>输入任务</dt>
            <dd class="mono">{{ shortId(task.params?.source_task_id) }}</dd>
            <dt>目标时间间隔</dt>
            <dd>{{ formatValue(task.params?.target_interval) }} 小时</dd>
            <dt>输入时间范围</dt>
            <dd>{{ formatValue(task.params?.input_time_range) }}</dd>
            <dt>输入格式</dt>
            <dd>{{ formatValue(task.params?.input_format) }}</dd>
          </template>
          <dt>提交时间</dt>
          <dd>{{ formatTime(task.created_at) }}</dd>
          <dt>完成时间</dt>
          <dd>{{ formatTime(task.finished_at) }}</dd>
        </dl>
      </div>

      <div class="card">
        <h2 class="card-title">结果文件</h2>
        <template v-if="ready">
          <dl class="kv drawer-kv">
            <dt>结果文件</dt>
            <dd class="mono">{{ payload.file_name || '—' }}</dd>
            <dt>结果类型</dt>
            <dd>{{ RESULT_TYPE_LABELS[payload.result_type] || payload.result_type || '—' }}</dd>
            <dt>文件数量</dt>
            <dd>{{ payload.file_count ?? 1 }}</dd>
          </dl>
          <p class="muted drawer-note">{{ payload.summary || '结果已生成。' }}</p>
          <p class="online-actions" style="margin-top: 16px">
            <a class="btn btn-primary" :href="downloadHref">下载结果</a>
          </p>
        </template>
        <p v-else-if="task.status === 'FAILED'" class="notice notice-error">
          {{ task.error || '任务执行失败，请查看任务详情。' }}
        </p>
        <p v-else class="muted">{{ result?.message || '结果尚未就绪。' }}</p>
      </div>
    </template>

    <p style="margin-top: 18px">
      <RouterLink class="btn" to="/tasks">← 返回任务中心</RouterLink>
    </p>
  </section>
</template>
