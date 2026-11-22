<script setup>
import { NAV_ITEMS } from '../router/nav'

// 子导航与顶部导航同源，避免两处各写一份。
const tabs = NAV_ITEMS.find((item) => item.key === 'online').children
</script>

<template>
  <section class="page">
    <header class="page-header">
      <h1>在线计算</h1>
      <p class="page-desc">
        使用 MetCom 完成多源气象资料获取与对流层参数计算：先取再分析资料，再反演对流层参数。中间过程（接口地址、文件目录、读取与插值方式）全部由后台处理。
      </p>
    </header>

    <nav class="subnav">
      <RouterLink v-for="tab in tabs" :key="tab.key" class="subnav-link" :to="tab.to">
        {{ tab.label }}
      </RouterLink>
    </nav>

    <RouterView />
  </section>
</template>
