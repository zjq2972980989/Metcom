<script setup>
/**
 * 内联线性图标（自绘，无第三方图标库、无位图素材）。
 * 全部沿用同一套风格：24 视框、1.6 描边、currentColor、圆头圆角。
 * 新增图标时在这里加一个 v-else-if 分支即可，全站复用。
 */
defineProps({
  name: { type: String, required: true },
  size: { type: [Number, String], default: 22 },
})
</script>

<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.6"
    stroke-linecap="round"
    stroke-linejoin="round"
    class="app-icon"
    aria-hidden="true"
  >
    <!-- 首页 -->
    <template v-if="name === 'home'">
      <path d="M4.3 10.6 12 4.2l7.7 6.4v8.3a1.6 1.6 0 0 1-1.6 1.6h-3.2v-5.2H9.1v5.2H5.9a1.6 1.6 0 0 1-1.6-1.6Z" />
    </template>

    <!-- 数据下载 / 在线计算：云 + 下行箭头 -->
    <template v-else-if="name === 'download-cloud'">
      <path d="M7.1 13.7h9.7a3.25 3.25 0 0 0 .2-6.5 4.85 4.85 0 0 0-9.3-1.15A3.65 3.65 0 0 0 7.1 13.7Z" />
      <path d="M12 14.6v5" />
      <path d="m9.7 17.4 2.3 2.3 2.3-2.3" />
    </template>

    <!-- 标准产品：标准化文件 -->
    <template v-else-if="name === 'file-lines'">
      <path d="M13.3 3.2H7.7A1.7 1.7 0 0 0 6 4.9v14.2a1.7 1.7 0 0 0 1.7 1.7h8.6a1.7 1.7 0 0 0 1.7-1.7V7.5Z" />
      <path d="M13.3 3.2v4.3H18" />
      <path d="M9.1 12.4h5.8M9.1 15.6h5.8" />
    </template>

    <!-- 定制产品：区域 / 测站 -->
    <template v-else-if="name === 'target'">
      <circle cx="12" cy="12" r="7.4" />
      <circle cx="12" cy="12" r="2.3" />
      <path d="M12 2.5v3.5M12 18v3.5M2.5 12H6M18 12h3.5" />
    </template>

    <!-- 任务中心：任务清单 -->
    <template v-else-if="name === 'list'">
      <path d="M8.6 5.3H7.3A1.7 1.7 0 0 0 5.6 7v11.4a1.7 1.7 0 0 0 1.7 1.7h9.4a1.7 1.7 0 0 0 1.7-1.7V7a1.7 1.7 0 0 0-1.7-1.7h-1.3" />
      <rect x="8.6" y="3.2" width="6.8" height="4" rx="1.3" />
      <path d="M9.2 11.9h5.6M9.2 15.3h5.6" />
    </template>

    <!-- 关于平台 -->
    <template v-else-if="name === 'info'">
      <circle cx="12" cy="12" r="8.3" />
      <path d="M12 11.1v5.3" />
      <path d="M12 7.9h.01" />
    </template>

    <!-- 搜索 -->
    <template v-else-if="name === 'search'">
      <circle cx="11" cy="11" r="6.4" />
      <path d="m15.7 15.7 4.1 4.1" />
    </template>

    <!-- 通知 -->
    <template v-else-if="name === 'bell'">
      <path d="M6.3 16h11.4l-1.5-2.2a2.4 2.4 0 0 1-.4-1.3V10a4.8 4.8 0 0 0-9.6 0v2.5a2.4 2.4 0 0 1-.4 1.3Z" />
      <path d="M10.2 16a1.8 1.8 0 0 0 3.6 0" />
    </template>

    <!-- 用户 -->
    <template v-else-if="name === 'user'">
      <circle cx="12" cy="8.4" r="3.6" />
      <path d="M4.9 20a7.3 7.3 0 0 1 14.2 0" />
    </template>

    <!-- 新增 / 创建 -->
    <template v-else-if="name === 'plus'">
      <path d="M12 5.4v13.2M5.4 12h13.2" />
    </template>

    <!-- 关闭 -->
    <template v-else-if="name === 'close'">
      <path d="m6.8 6.8 10.4 10.4M17.2 6.8 6.8 17.2" />
    </template>

    <!-- 大气分层（列表与装饰） -->
    <template v-else-if="name === 'layers'">
      <path d="M4 9.6h16M5.6 13.8h12.8M8 18h9" />
    </template>
  </svg>
</template>
