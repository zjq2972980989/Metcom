<script setup>
/**
 * 核心能力卡片的超淡科研线稿装饰。
 * 四个主题各对应一张卡片的能力：数据获取 / 参数反演 / 产品服务 / 结果分析。
 * 全部是内联线性路径（220 × 170 视框、统一描边、currentColor、无填充、无位图），
 * 透明度由 .cap-decor 控制（约 8%），只作为背景纹理，不参与布局与交互。
 */
defineProps({
  name: { type: String, required: true },
})
</script>

<template>
  <svg
    class="cap-decor"
    viewBox="0 0 220 170"
    fill="none"
    stroke="currentColor"
    stroke-width="1.4"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
  >
    <!-- 01 数据获取：多个来源汇入数据仓 -->
    <template v-if="name === 'sources'">
      <ellipse cx="152" cy="46" rx="46" ry="16" />
      <path d="M106 46v44a46 16 0 0 0 92 0V46" />
      <path d="M106 68a46 16 0 0 0 92 0" />
      <circle cx="30" cy="38" r="9" />
      <circle cx="30" cy="86" r="9" />
      <circle cx="30" cy="134" r="9" />
      <path d="M39 38h24a32 32 0 0 1 32 32v2" />
      <path d="M39 86h34" />
      <path d="M39 134h24a32 32 0 0 0 32-32v-2" />
    </template>

    <!-- 02 参数反演：观测点与拟合曲线 -->
    <template v-else-if="name === 'curve'">
      <path d="M16 22v124h190" />
      <path d="M20 124C44 120 46 78 72 72c26-6 34 24 60 14 26-10 28-46 54-56" />
      <circle cx="20" cy="124" r="3.2" />
      <circle cx="72" cy="72" r="3.2" />
      <circle cx="132" cy="86" r="3.2" />
      <circle cx="186" cy="30" r="3.2" />
    </template>

    <!-- 03 产品服务：成册的数据产品 -->
    <template v-else-if="name === 'docs'">
      <path d="M56 44v92a12 12 0 0 0 12 12h58" />
      <path d="M92 20h44l26 26v96a10 10 0 0 1-10 10H92a10 10 0 0 1-10-10V30a10 10 0 0 1 10-10Z" />
      <path d="M136 20v26h26" />
      <path d="M100 80h54M100 102h54M100 124h30" />
    </template>

    <!-- 04 结果分析：空间网格与时间序列 -->
    <template v-else-if="name === 'grid'">
      <path d="M32 34v108M68 34v108M104 34v108M140 34v108M176 34v108" />
      <path d="M32 34h144M32 70h144M32 106h144M32 142h144" />
      <path d="M40 126 82 100 118 108 154 64 196 44" />
      <circle cx="82" cy="100" r="3.2" />
      <circle cx="154" cy="64" r="3.2" />
    </template>
  </svg>
</template>
