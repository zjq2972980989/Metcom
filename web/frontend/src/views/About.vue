<script setup>
import { computed, onMounted } from 'vue'

import { endpoints, API_BASE } from '../api/client'
import { useApi } from '../composables/useApi'

const health = useApi(endpoints.health)
const engine = useApi(endpoints.engine)
const meta = useApi(endpoints.meta)

onMounted(() => {
  health.load()
  engine.load()
  meta.load()
})

const sources = computed(() => meta.data.value?.sources ?? [])
const parameters = computed(() => meta.data.value?.parameters ?? [])
</script>

<template>
  <section class="page">
    <header class="page-header">
      <h1>关于 MetCom</h1>
      <p class="page-desc">多源气象再分析资料对流层参数精细反演平台</p>
    </header>

    <div class="card">
      <h2 class="card-title">平台简介</h2>
      <p class="muted">
        把现有 Python MetCom 的计算能力封装成 Web 服务，让用户不必直接操作 Python、配置文件或命令行，
        就能完成：数据获取 → 参数计算 → 标准产品查询 → 定制产品计算 → 任务管理 → 结果预览 → 数据下载。
      </p>
      <p class="muted" style="margin-top: 10px">
        核心计算全部由现有 MetCom 引擎执行，Web 平台只负责组织输入、调度任务与呈现结果。
      </p>
      <span class="chip">当前版本：V1.0 · 阶段 3 在线计算</span>
    </div>

    <div class="card">
      <h2 class="card-title">核心功能</h2>
      <p>数据下载 · 参数计算 · 标准产品 · 定制产品</p>
    </div>

    <div class="card">
      <h2 class="card-title">数据源</h2>
      <dl v-if="sources.length" class="kv">
        <template v-for="source in sources" :key="source.key">
          <dt>{{ source.name }}</dt>
          <dd>
            {{ source.time_resolution }} 时间分辨率 · {{ source.spatial_resolution }} 空间分辨率 ·
            {{ source.levels }} 层
          </dd>
        </template>
      </dl>
      <p v-else class="muted">后端未连接，暂时无法读取数据源清单。</p>
    </div>

    <div class="card">
      <h2 class="card-title">输出参数</h2>
      <ul v-if="parameters.length" class="list">
        <li v-for="item in parameters" :key="item.key">
          {{ item.key }}（{{ item.name }}，{{ item.unit }}）—— {{ item.description }}
        </li>
      </ul>
      <p v-else class="muted">后端未连接，暂时无法读取参数清单。</p>
    </div>

    <div class="card">
      <h2 class="card-title">技术信息</h2>
      <dl class="kv">
        <dt>前端</dt>
        <dd>Vue 3 + Vite</dd>
        <dt>后端</dt>
        <dd>FastAPI（{{ API_BASE }}）</dd>
        <dt>后端状态</dt>
        <dd>
          <template v-if="health.loaded.value">
            {{ health.httpOk.value ? `正常 · v${health.data.value?.version}` : health.error.value }}
          </template>
          <template v-else>检测中…</template>
        </dd>
        <dt>计算引擎</dt>
        <dd>
          <template v-if="engine.loaded.value">
            <template v-if="engine.data.value?.available">
              Python MetCom {{ engine.data.value.version }}
            </template>
            <template v-else>{{ engine.data.value?.detail || engine.error.value }}</template>
          </template>
          <template v-else>检测中…</template>
        </dd>
      </dl>
    </div>
  </section>
</template>
