<script setup>
import AppSidebar from '../components/AppSidebar.vue'

const year = new Date().getFullYear()
</script>

<template>
  <div class="app-shell">
    <AppSidebar />

    <div class="app-body">
      <main class="app-main">
        <RouterView />
      </main>

      <footer class="app-footer">
        <div class="app-footer-inner">
          <div class="footer-brand">
            <span class="footer-name">MetCom</span>
            <span class="footer-fullname">多源气象再分析资料对流层参数精细反演平台</span>
          </div>
          <span class="footer-meta">V1.0 · 阶段 3 在线计算 · {{ year }}</span>
        </div>
      </footer>
    </div>
  </div>
</template>
