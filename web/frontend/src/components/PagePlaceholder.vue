<script setup>
import { computed } from 'vue'

import { api } from '../api/client'
import { useApi } from '../composables/useApi'

/**
 * 阶段一通用骨架页：说明本页规划 + 真实调用后端接口验证连通。
 * 该接口在阶段一按设计返回 501，说明"契约已固定、实现待排期"。
 */
const props = defineProps({
  title: { type: String, required: true },
  description: { type: String, default: '' },
  phase: { type: String, required: true },
  endpoint: { type: String, default: '' },
  method: { type: String, default: 'GET' },
  points: { type: Array, default: () => [] },
  standalone: { type: Boolean, default: true },
})

const { data, error, status, reachable, httpOk, loading, loaded, load } = useApi(() =>
  props.method === 'POST' ? api.post(props.endpoint) : api.get(props.endpoint),
)

const output = computed(() => {
  if (!loaded.value) return '（点击上方按钮调用；阶段一业务接口预期返回 501 占位响应）'
  if (httpOk.value) return JSON.stringify(data.value, null, 2)
  if (reachable.value) return `HTTP ${status.value}\n${error.value}`
  return error.value
})
</script>

<template>
  <component :is="standalone ? 'section' : 'div'" :class="standalone ? 'page' : ''">
    <header class="page-header">
      <h1>{{ title }}</h1>
      <p v-if="description" class="page-desc">{{ description }}</p>
      <span class="chip">计划实现：{{ phase }}</span>
    </header>

    <div v-if="points.length" class="card">
      <h2 class="card-title">本页规划要点</h2>
      <ul class="list">
        <li v-for="point in points" :key="point">{{ point }}</li>
      </ul>
    </div>

    <div v-if="endpoint" class="card">
      <h2 class="card-title">接口连通检查</h2>
      <p class="muted mono">{{ method }} {{ endpoint }}</p>
      <p style="margin-top: 14px">
        <button class="btn" :disabled="loading" @click="load">
          {{ loading ? '调用中…' : '调用接口' }}
        </button>
      </p>
      <pre class="output">{{ output }}</pre>
    </div>
  </component>
</template>