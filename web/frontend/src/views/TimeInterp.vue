<script setup>
import { computed, onMounted, ref } from 'vue'

import { getTimeInterpInputs, submitTimeInterp } from '../api/online'

// 时间插值：输入是平台自己已完成的参数计算结果（按任务选择），插值算法由现有
// Python MetCom 引擎的 metcom.timeinterp 提供；本页只做选择与提交，不重算数值。
//
// 输入清单、可选间隔、"不可插值"原因全部来自后端 /api/timeinterp/inputs，
// 前端不写死任何能力判断，也不展示内部路径 / 脚本 / 临时目录。
const inputs = ref([])
const targetIntervals = ref([1])
const sourceTaskId = ref('')
const targetInterval = ref(1)
const loading = ref(true)
const loadError = ref('')
const submitting = ref(false)
const error = ref('')
const submitted = ref(null)

const selected = computed(
  () => inputs.value.find((item) => item.task_id === sourceTaskId.value) || null,
)
const canSubmit = computed(
  () => Boolean(sourceTaskId.value) && selected.value?.interpolatable === true && !submitting.value,
)

function formatTime(iso) {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return String(iso)
  const pad = (value) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function formatRange(labels) {
  if (!Array.isArray(labels) || labels.length === 0) return '—'
  const first = formatTime(labels[0])
  const last = formatTime(labels[labels.length - 1])
  return first === last ? first : `${first} ~ ${last}`
}

function inputLabel(item) {
  if (!item.interpolatable) return `${item.name} · ${item.dataset_name}（不可插值）`
  return `${item.name} · ${item.dataset_name} · ${item.hour_count} 个时次 / ${item.site_count} 个站点`
}

onMounted(async () => {
  try {
    const payload = await getTimeInterpInputs()
    inputs.value = payload?.items ?? []
    targetIntervals.value = payload?.target_intervals ?? [1]
    targetInterval.value = payload?.default_target_interval ?? 1
    sourceTaskId.value = inputs.value.find((item) => item.interpolatable)?.task_id ?? ''
  } catch (err) {
    loadError.value = err?.message || '输入结果读取失败，请稍后重试。'
  } finally {
    loading.value = false
  }
})

async function onSubmit() {
  if (!canSubmit.value) return
  submitting.value = true
  error.value = ''
  submitted.value = null
  try {
    submitted.value = await submitTimeInterp({
      sourceTaskId: sourceTaskId.value,
      targetInterval: targetInterval.value,
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
    <h2 class="card-title">时间插值</h2>
    <p class="muted task-create-note">
      把已完成的反演结果插值到更密的时次（例如 6 小时间隔插到逐小时）。插值由现有 Python
      MetCom 引擎按原有算法完成，原始结果文件不会被修改，插值结果写入本次任务自己的目录。
    </p>

    <p v-if="loading" class="muted">读取中…</p>
    <p v-else-if="loadError" class="notice notice-error">{{ loadError }}</p>
    <p v-else-if="inputs.length === 0" class="notice">
      暂无可用于插值的输入结果。请先在「参数计算」完成一次包含全部 8 个参数的
      多时次反演，再回到本页。
    </p>

    <form v-else class="online-form" @submit.prevent="onSubmit">
      <label class="field">
        <span class="field-label">输入结果</span>
        <select v-model="sourceTaskId" class="input">
          <option
            v-for="item in inputs"
            :key="item.task_id"
            :value="item.task_id"
            :disabled="!item.interpolatable"
          >
            {{ inputLabel(item) }}
          </option>
        </select>
        <span class="field-note">
          只列出已完成的参数计算结果，每一项对应任务中心里的一个任务。
        </span>
      </label>

      <div v-if="selected" class="field">
        <span class="field-label">输入信息</span>
        <dl class="kv">
          <dt>数据源</dt>
          <dd>{{ selected.dataset_name || '—' }}</dd>
          <dt>结果格式</dt>
          <dd>{{ (selected.result_format || '—').toUpperCase() }}</dd>
          <dt>时次数</dt>
          <dd>
            {{ selected.hour_count }} 个（每天 {{ selected.min_hours_per_day }} ~
            {{ selected.max_hours_per_day }} 个）
          </dd>
          <dt>站点数</dt>
          <dd>{{ selected.site_count }}</dd>
          <dt>时间范围</dt>
          <dd>{{ formatRange(selected.time_labels) }}</dd>
          <dt>插值参数</dt>
          <dd>{{ (selected.parameters || []).join('、') || '—' }}</dd>
        </dl>
        <span v-if="!selected.interpolatable" class="field-note">
          该结果不可插值：{{ selected.reason }}
        </span>
      </div>

      <div class="field">
        <span class="field-label">目标时间间隔</span>
        <div class="time-modes">
          <label v-for="value in targetIntervals" :key="value" class="time-mode">
            <input v-model="targetInterval" type="radio" :value="value" />{{ value }} 小时
          </label>
        </div>
        <span class="field-note">
          引擎按输入结果已有的时次与所选间隔生成目标时次；跨天时，当日结果会延伸到次日 00 时。
          8 个参数固定全部输出，不提供只插值部分参数。
        </span>
      </div>

      <p v-if="error" class="notice notice-error">{{ error }}</p>
      <p v-else-if="submitted" class="notice notice-ok">
        任务已提交：{{ submitted.name }}（{{ submitted.status }}）。
        <RouterLink :to="`/preview/${submitted.task_id}`">查看结果</RouterLink>
        <RouterLink to="/tasks">前往任务中心</RouterLink>
      </p>

      <div class="online-actions">
        <button class="btn btn-primary" type="submit" :disabled="!canSubmit">
          {{ submitting ? '提交中…' : '开始插值' }}
        </button>
        <span class="field-note">提交后立即返回，不在此页等待；任务进度请在任务中心查看。</span>
      </div>
    </form>
  </div>
</template>
