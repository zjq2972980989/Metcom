<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

import { ApiError, endpoints } from '../api/client'
import { createTask, getTaskResult, getTasks } from '../api/tasks'
import AppIcon from '../components/AppIcon.vue'
import { useApi } from '../composables/useApi'

/** 轮询间隔：只在存在 PENDING / RUNNING 任务时开启，任务全部结束后自动停止。 */
const POLL_MS = 2000

const FILTERS = [
  { key: 'ALL', label: '全部' },
  { key: 'DOWNLOAD', label: '数据下载' },
  { key: 'CALCULATION', label: '参数计算' },
  { key: 'TIMEINTERP', label: '时间插值' },
  { key: 'CUSTOM', label: '定制产品' },
]

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
  timeinterp: '插值结果',
}
const MODE_LABELS = { grid: '格网模式', station: '站点模式' }

// 详情里只展示用户看得懂的业务参数；内部路径 / 脚本 / 方法名一律不暴露。
const PARAM_LABELS = {
  dataset: '数据源',
  parameters: '参数',
  variables: '变量',
  time: '时间',
  start_time: '开始时间',
  end_time: '结束时间',
  time_mode: '时间方式',
  mode: '计算模式',
  region: '区域',
  stations: '站点',
  sites: '测站',
  levels: '层',
  resolution: '分辨率',
  source_task_id: '输入任务',
  target_interval: '目标间隔(小时)',
  input_format: '输入格式',
  input_hours: '输入时次数',
  input_doy_folders: '输入年积日',
  input_time_range: '输入时间范围',
  sites_count: '站点数',
}
const PARAM_HIDDEN = new Set(['simulate_failure'])
const INTERNAL_KEY = /(^|_)(path|dir|script|config|method|token|key|url)$/

/* ------------------------------------------------------------------ 列表数据 -- */
const tasks = ref([])
const total = ref(0)
const loading = ref(false)
const loadedOnce = ref(false)
const listError = ref('')
const filter = ref('ALL')

let pollTimer = null
let inFlight = false
let requestSeq = 0

// 数据源显示名不在前端写死，直接用后端 /api/meta 的清单（拿不到就退回原始值）。
const meta = useApi(endpoints.meta)
const sources = computed(() => meta.data.value?.sources ?? [])
const datasetNames = computed(() => {
  const map = {}
  for (const source of sources.value) map[source.key] = source.name
  return map
})

const hasActive = computed(() =>
  tasks.value.some((task) => task.status === 'PENDING' || task.status === 'RUNNING'),
)

async function refresh({ silent = false } = {}) {
  if (inFlight) return
  inFlight = true
  const seq = ++requestSeq
  if (!silent) loading.value = true

  try {
    const payload = await getTasks({ type: filter.value === 'ALL' ? undefined : filter.value })
    if (seq !== requestSeq) return
    tasks.value = payload?.items ?? []
    total.value = payload?.total ?? tasks.value.length
    listError.value = ''
  } catch (err) {
    if (seq !== requestSeq) return
    listError.value = err instanceof ApiError ? err.message : String(err)
  } finally {
    inFlight = false
    loading.value = false
    loadedOnce.value = true
    syncPolling()
  }
}

function syncPolling() {
  if (hasActive.value) {
    if (pollTimer === null) {
      pollTimer = window.setInterval(() => {
        if (!document.hidden) refresh({ silent: true })
      }, POLL_MS)
    }
  } else if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
}

function stopPolling() {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
}

watch(filter, () => refresh())

/* ------------------------------------------------------------------ 展示映射 -- */
function typeLabel(type) {
  return TYPE_LABELS[type] || type || '—'
}

function statusLabel(status) {
  return STATUS_LABELS[status] || status || '—'
}

function actionLabel(status) {
  if (status === 'SUCCESS') return '查看结果'
  if (status === 'FAILED') return '查看错误'
  return '查看状态'
}

function datasetLabel(value) {
  if (value === null || value === undefined || value === '') return '—'
  const key = String(value)
  return datasetNames.value[key] || datasetNames.value[key.toLowerCase()] || key
}

function shortId(id) {
  return typeof id === 'string' ? id.slice(0, 8) : ''
}

function formatTime(iso) {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return String(iso)
  const pad = (value) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function formatParamValue(key, value) {
  if (Array.isArray(value)) {
    if (key === 'sites') return value.length ? `${value.length} 个测站` : '—'
    return value.length ? value.join('、') : '—'
  }
  if (value && typeof value === 'object') return JSON.stringify(value)
  if (key === 'mode') return MODE_LABELS[value] || String(value)
  if (key === 'dataset') return datasetLabel(value)
  if (key === 'source_task_id') return shortId(value)
  return String(value)
}

const paramRows = computed(() => {
  const params = selected.value?.params
  if (!params || typeof params !== 'object') return []
  return Object.entries(params)
    .filter(([key, value]) => {
      if (PARAM_HIDDEN.has(key) || INTERNAL_KEY.test(key)) return false
      return value !== null && value !== undefined && value !== ''
    })
    .map(([key, value]) => ({
      key,
      label: PARAM_LABELS[key] || key,
      value: formatParamValue(key, value),
    }))
})

/* ------------------------------------------------------------------ 详情抽屉 -- */
const selectedId = ref('')
const detailResult = ref(null)
const detailError = ref('')
const detailLoading = ref(false)

const selected = computed(() => tasks.value.find((task) => task.id === selectedId.value) || null)

function openDetail(task) {
  selectedId.value = task.id
  detailResult.value = null
  detailError.value = ''
  if (task.status === 'SUCCESS') loadResult(task.id)
}

function closeDetail() {
  selectedId.value = ''
  detailResult.value = null
  detailError.value = ''
}

async function loadResult(taskId) {
  detailLoading.value = true
  detailError.value = ''
  try {
    const payload = await getTaskResult(taskId)
    if (selectedId.value !== taskId) return
    detailResult.value = payload
  } catch (err) {
    if (selectedId.value !== taskId) return
    detailError.value = err instanceof ApiError ? err.message : String(err)
  } finally {
    if (selectedId.value === taskId) detailLoading.value = false
  }
}

// 任务在抽屉打开期间跑完（轮询更新了列表），结果自动补上，不需要用户手动刷新。
watch(
  () => selected.value?.status,
  (status, previous) => {
    if (status === 'SUCCESS' && previous && previous !== 'SUCCESS') loadResult(selectedId.value)
  },
)

function onKeydown(event) {
  if (event.key === 'Escape') closeDetail()
}

/* ---------------------------------------------------------------- 开发测试入口 -- */
const showCreate = ref(false)
const creating = ref(false)
const createError = ref('')
const form = reactive({ name: '', type: 'CALCULATION', dataset: '', simulateFailure: false })

function toggleCreate() {
  showCreate.value = !showCreate.value
  createError.value = ''
}

async function submitTestTask() {
  creating.value = true
  createError.value = ''
  try {
    const params = {}
    if (form.dataset) params.dataset = form.dataset
    if (form.type === 'CALCULATION') params.parameters = ['PWV', 'ZTD']
    if (form.type === 'CUSTOM') params.mode = 'grid'
    if (form.simulateFailure) params.simulate_failure = true

    await createTask({
      name: form.name.trim() || `${TYPE_LABELS[form.type]}测试任务`,
      type: form.type,
      params,
    })

    showCreate.value = false
    form.name = ''
    form.dataset = ''
    form.simulateFailure = false
    filter.value = 'ALL'
    await refresh()
  } catch (err) {
    createError.value = err instanceof ApiError ? err.message : String(err)
  } finally {
    creating.value = false
  }
}

onMounted(() => {
  meta.load()
  refresh()
  window.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  stopPolling()
  window.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <section class="page">
    <header class="page-header task-head">
      <div>
        <h1>任务中心</h1>
        <p class="page-desc">统一查看数据下载、参数计算与定制产品任务</p>
      </div>
      <div class="task-head-actions">
        <span class="task-dev-tag">开发测试</span>
        <button class="btn" type="button" @click="toggleCreate">
          <AppIcon name="plus" :size="15" />
          {{ showCreate ? '收起测试入口' : '创建测试任务' }}
        </button>
      </div>
    </header>

    <form v-if="showCreate" class="card task-create" @submit.prevent="submitTestTask">
      <h2 class="card-title">创建测试任务</h2>
      <p class="muted task-create-note">
        仅用于验证「创建 → 后台执行 → 状态流转」这条链路，不会调用真实 MetCom 计算。
      </p>

      <div class="task-create-grid">
        <label class="field">
          <span class="field-label">任务名称</span>
          <input
            v-model="form.name"
            class="input"
            type="text"
            maxlength="80"
            placeholder="例如：测试参数计算"
          />
        </label>

        <label class="field">
          <span class="field-label">任务类型</span>
          <select v-model="form.type" class="input">
            <option value="DOWNLOAD">数据下载</option>
            <option value="CALCULATION">参数计算</option>
            <option value="CUSTOM">定制产品</option>
          </select>
        </label>

        <label class="field">
          <span class="field-label">数据源（可选）</span>
          <select v-model="form.dataset" class="input">
            <option value="">不指定</option>
            <option v-for="source in sources" :key="source.key" :value="source.key">
              {{ source.name }}
            </option>
          </select>
        </label>
      </div>

      <label class="task-check">
        <input v-model="form.simulateFailure" type="checkbox" />
        <span>模拟任务失败（用于验证 FAILED 状态与错误展示）</span>
      </label>

      <p v-if="createError" class="task-error">{{ createError }}</p>

      <div class="task-create-actions">
        <button class="btn btn-primary" type="submit" :disabled="creating">
          {{ creating ? '提交中…' : '创建任务' }}
        </button>
        <button class="btn" type="button" :disabled="creating" @click="showCreate = false">
          取消
        </button>
      </div>
    </form>

    <div class="task-filters" role="tablist" aria-label="任务类型筛选">
      <button
        v-for="item in FILTERS"
        :key="item.key"
        class="task-filter"
        :class="{ active: filter === item.key }"
        type="button"
        role="tab"
        :aria-selected="filter === item.key"
        @click="filter = item.key"
      >
        {{ item.label }}
      </button>
    </div>

    <div class="card task-panel">
      <div class="task-panel-head">
        <span class="muted">共 {{ total }} 个任务</span>
        <button class="btn task-refresh" type="button" :disabled="loading" @click="refresh()">
          {{ loading ? '刷新中…' : '刷新' }}
        </button>
      </div>

      <p v-if="listError" class="task-error task-panel-error">{{ listError }}</p>

      <div v-else-if="tasks.length" class="task-table" role="table">
        <div class="task-table-head" role="row">
          <span role="columnheader">任务名称</span>
          <span role="columnheader">任务类型</span>
          <span role="columnheader">数据源</span>
          <span role="columnheader">提交时间</span>
          <span role="columnheader">状态</span>
          <span role="columnheader">操作</span>
        </div>

        <div v-for="task in tasks" :key="task.id" class="task-row" role="row">
          <div class="task-cell" data-label="任务名称">
            <span class="task-name">{{ task.name }}</span>
            <span class="task-id mono">{{ shortId(task.id) }}</span>
          </div>
          <div class="task-cell" data-label="任务类型">{{ typeLabel(task.type) }}</div>
          <div class="task-cell" data-label="数据源">{{ datasetLabel(task.params?.dataset) }}</div>
          <div class="task-cell" data-label="提交时间">{{ formatTime(task.created_at) }}</div>
          <div class="task-cell" data-label="状态">
            <span class="status" :class="`is-${String(task.status).toLowerCase()}`">
              <span class="status-dot" aria-hidden="true"></span>{{ statusLabel(task.status) }}
            </span>
          </div>
          <div class="task-cell task-cell-action" data-label="操作">
            <button class="btn task-action" type="button" @click="openDetail(task)">
              {{ actionLabel(task.status) }}
            </button>
          </div>
        </div>
      </div>

      <p v-else-if="loading && !loadedOnce" class="task-empty">加载中…</p>
      <p v-else class="task-empty">
        暂无任务。点击右上角「创建测试任务」提交一个，验证任务链路。
      </p>
    </div>

    <Teleport to="body">
      <div v-if="selected" class="drawer-mask" @click="closeDetail"></div>
      <aside
        v-if="selected"
        class="drawer"
        role="dialog"
        aria-modal="true"
        :aria-label="`任务详情：${selected.name}`"
      >
        <header class="drawer-head">
          <div>
            <h2 class="drawer-title">{{ selected.name }}</h2>
            <p class="drawer-sub mono">{{ selected.id }}</p>
          </div>
          <button class="drawer-close" type="button" aria-label="关闭" @click="closeDetail">
            <AppIcon name="close" :size="16" />
          </button>
        </header>

        <div class="drawer-body">
          <div class="drawer-status">
            <span class="status" :class="`is-${String(selected.status).toLowerCase()}`">
              <span class="status-dot" aria-hidden="true"></span>{{ statusLabel(selected.status) }}
            </span>
            <span class="drawer-progress">{{ selected.progress }}%</span>
          </div>
          <div class="progress">
            <span
              class="progress-fill"
              :class="`is-${String(selected.status).toLowerCase()}`"
              :style="{ width: `${selected.progress}%` }"
            ></span>
          </div>
          <p class="muted drawer-message">{{ selected.message || '—' }}</p>

          <dl class="kv drawer-kv">
            <dt>任务类型</dt>
            <dd>{{ typeLabel(selected.type) }}</dd>
            <dt>数据源</dt>
            <dd>{{ datasetLabel(selected.params?.dataset) }}</dd>
            <dt>创建时间</dt>
            <dd>{{ formatTime(selected.created_at) }}</dd>
            <dt>开始时间</dt>
            <dd>{{ formatTime(selected.started_at) }}</dd>
            <dt>完成时间</dt>
            <dd>{{ formatTime(selected.finished_at) }}</dd>
          </dl>

          <section v-if="paramRows.length" class="drawer-section">
            <h3 class="drawer-section-title">任务参数</h3>
            <dl class="kv drawer-kv">
              <template v-for="row in paramRows" :key="row.key">
                <dt>{{ row.label }}</dt>
                <dd>{{ row.value }}</dd>
              </template>
            </dl>
          </section>

          <section v-if="selected.status === 'FAILED'" class="drawer-section">
            <h3 class="drawer-section-title">错误信息</h3>
            <p class="drawer-alert">
              {{ selected.error || '任务执行失败，请查看任务详情。' }}
            </p>
          </section>

          <section v-if="selected.status === 'SUCCESS'" class="drawer-section">
            <h3 class="drawer-section-title">任务结果</h3>
            <p v-if="detailLoading" class="muted">读取中…</p>
            <p v-else-if="detailError" class="drawer-alert">{{ detailError }}</p>
            <template v-else-if="detailResult?.ready && detailResult.result">
              <dl class="kv drawer-kv">
                <dt>结果文件</dt>
                <dd class="mono">{{ detailResult.result.file_name || '—' }}</dd>
                <dt>结果类型</dt>
                <dd>{{ RESULT_TYPE_LABELS[detailResult.result.result_type] || detailResult.result.result_type || '—' }}</dd>
              </dl>
              <p class="muted drawer-note">{{ detailResult.result.summary || '结果已生成。' }}</p>
            </template>
            <p v-else class="muted">{{ detailResult?.message || '结果尚未就绪。' }}</p>
          </section>
        </div>
      </aside>
    </Teleport>
  </section>
</template>
