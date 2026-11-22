<script setup>
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'

import logoLightUrl from '../assets/logo-metcom-light.png'
import logoMarkUrl from '../assets/logo-metcom-mark.png'
import AppIcon from './AppIcon.vue'
import { endpoints } from '../api/client'
import { useApi } from '../composables/useApi'
import { NAV_ITEMS } from '../router/nav'

const route = useRoute()
const activeKey = computed(() => route.meta?.nav || '')

// 常驻连通指示：真实调用 /api/health，失败即说明后端未启动。
const { httpOk, loaded, reachable, load } = useApi(endpoints.health)

onMounted(load)

const dotClass = computed(() => {
  if (!loaded.value) return ''
  return httpOk.value ? 'ok' : 'bad'
})

const statusText = computed(() => {
  if (!loaded.value) return '检测中'
  if (httpOk.value) return 'API 正常'
  return reachable.value ? 'API 异常' : 'API 未连接'
})
</script>

<template>
  <aside class="app-sidebar">
    <RouterLink to="/" class="side-brand" aria-label="MetCom 首页">
      <picture>
        <source media="(max-width: 640px)" :srcset="logoMarkUrl" />
        <img class="side-logo" :src="logoLightUrl" alt="MetCom" width="900" height="190" />
      </picture>
    </RouterLink>

    <nav class="side-nav">
      <template v-for="item in NAV_ITEMS" :key="item.key">
        <div
          v-if="item.children"
          class="side-group"
          :class="{ 'is-open': activeKey === item.key }"
        >
          <RouterLink
            class="side-link"
            :class="{ active: activeKey === item.key }"
            :to="item.to"
          >
            <AppIcon :name="item.icon" :size="18" />
            <span class="side-label">{{ item.label }}</span>
          </RouterLink>

          <div class="side-sub">
            <RouterLink
              v-for="child in item.children"
              :key="child.key"
              class="side-sub-link"
              :to="child.to"
            >
              {{ child.label }}
            </RouterLink>
          </div>
        </div>

        <RouterLink
          v-else
          class="side-link"
          :class="{ active: activeKey === item.key }"
          :to="item.to"
        >
          <AppIcon :name="item.icon" :size="18" />
          <span class="side-label">{{ item.label }}</span>
        </RouterLink>
      </template>
    </nav>

    <div class="side-foot">
      <span class="side-status">
        <span class="dot" :class="dotClass"></span>
        <span class="side-label">{{ statusText }}</span>
      </span>
    </div>
  </aside>
</template>
