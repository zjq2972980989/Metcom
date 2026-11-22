<script setup>
import earthUrl from '../assets/hero-earth.jpg'
import logoLightUrl from '../assets/logo-metcom-light.png'
import AppIcon from '../components/AppIcon.vue'
import capDataSources from '../assets/images/capabilities/cap-01-data-sources.jpg'
import capInversion from '../assets/images/capabilities/cap-02-parameter-inversion.jpg'
import capProducts from '../assets/images/capabilities/cap-03-products.jpg'
import capAnalysis from '../assets/images/capabilities/cap-04-analysis.jpg'

// 首页结构固定为：Hero → 核心能力 → 多源数据 → 对流层参数。
// Hero 是沉浸式图片区：真实地球影像 + 深蓝透明蒙版 + 左对齐白色内容，不使用白色卡片。
// 阶段一不接后端，下面的清单与 /api/meta 一致，仅作展示。

/*
 * 核心能力卡片背景图：全部为 NASA 公共领域（Public Domain）真实气象 / 卫星素材，
 * 无第三方水印、Logo 或署名，仅做等比裁切（cover）后本地保存，未调色、未合成。
 * 完整记录见 src/assets/images/capabilities/CREDITS.md
 *
 * 01 数据获取  Source: NASA GSFC / MODIS — "Satellite Eyes First Major Atlantic Hurricane in 3 Years: Gonzalo"
 *              https://images.nasa.gov/details/GSFC_20171208_Archive_e000947   License: Public Domain
 * 02 参数反演  Source: NASA / ISS Expedition 23 — "Earth Observations taken by the Expedition 23 Crew"（大气层边缘）
 *              https://images.nasa.gov/details/iss023e057948                    License: Public Domain
 * 03 产品服务  Source: NASA Earth Observatory — "Cloudy Earth"（全球云量产品）
 *              https://images.nasa.gov/details/cloudy-earth_16810027983_o        License: Public Domain
 * 04 结果分析  Source: NASA JPL — "COWVR and TEMPEST Image Hurricane Ian"（微波反演海面风场产品）
 *              https://images.nasa.gov/details/PIA25425                          License: Public Domain
 */

const capabilities = [
  {
    index: '01',
    title: '数据获取',
    en: 'DATA ACCESS',
    desc: '多源再分析资料统一接入',
    tags: ['ERA5', 'MERRA-2', 'CRA40', 'CRA V1.5', 'CMA-MESO'],
    image: capDataSources,
    veil: '', // 亮暗适中：使用基础蒙版
    tone: '', // 原图明暗适中，不做额外修正
  },
  {
    index: '02',
    title: '参数反演',
    en: 'PARAMETER RETRIEVAL',
    desc: '对流层参数精细计算',
    tags: ['PWV', 'WVD', 'Tm', 'ZTD', 'ZHD', 'ZWD'],
    image: capInversion,
    veil: 'v-deep', // 影像本身很暗，蒙版放一档以保留大气层细节
    tone: '', // 原图明暗适中，不做额外修正
  },
  {
    index: '03',
    title: '产品服务',
    en: 'PRODUCT SERVICE',
    desc: '标准产品与定制产品服务',
    tags: ['标准产品', '定制产品', '数据下载'],
    image: capProducts,
    veil: 'v-bright', // 全球云量产品整幅偏亮，蒙版压一档保证白字清晰
    tone: 't-flat', // 原图偏灰、对比度低：提高对比并轻微压暗，视觉重量与其他三张拉齐
  },
  {
    index: '04',
    title: '结果分析',
    en: 'RESULT ANALYSIS',
    desc: '多维结果预览与分析',
    tags: ['空间分布', '时间序列', '结果预览'],
    image: capAnalysis,
    veil: 'v-deep', // 左半幅极暗，蒙版放一档
    tone: 't-rich', // 原图高饱和荧光色过抢：降低饱和度，避免压过白字
  },
]

// 首页只做轻量能力介绍：数据源 / 数据类型 / 时间尺度。
// 空间分辨率、垂直层数等详细规格不在首页展示，后续放到标准产品页面。
// CMA-MESO 属区域数值预报，不与其余四源同称再分析资料。
const sources = [
  { name: 'ERA5', type: '全球再分析', scale: '逐小时' },
  { name: 'MERRA-2', type: '全球再分析', scale: '逐6小时' },
  { name: 'CRA40', type: '全球再分析', scale: '逐6小时' },
  { name: 'CRA V1.5', type: '全球再分析', scale: '逐小时' },
  { name: 'CMA-MESO', type: '区域数值预报', scale: '逐3小时起报' },
]

const parameters = [
  { key: 'PWV', name: '大气可降水量', unit: 'mm' },
  { key: 'WVD', name: '水汽密度', unit: 'g/m³' },
  { key: 'Tm', name: '大气加权平均温度', unit: 'K' },
  { key: 'ZTD', name: '天顶总延迟', unit: 'm' },
  { key: 'ZHD', name: '天顶静力延迟', unit: 'm' },
  { key: 'ZWD', name: '天顶湿延迟', unit: 'm' },
]
</script>

<template>
  <div class="home">
    <!-- Hero：真实地球影像打底 + 深蓝透明蒙版 + 直接叠加在图片上的白色内容 -->
    <section class="hero">
      <div
        class="hero-media"
        :style="{ backgroundImage: `url(${earthUrl})` }"
        aria-hidden="true"
      ></div>
      <div class="hero-overlay" aria-hidden="true"></div>

      <!-- 内容区顶栏：透明地叠在 Hero 图片上，不铺任何遮罩 -->
      <div class="hero-topbar">
        <div class="topbar-search">
          <AppIcon name="search" :size="17" />
          <input
            class="topbar-input"
            type="text"
            placeholder="搜索产品、数据源或关键词..."
            aria-label="搜索产品、数据源或关键词"
          />
        </div>

        <div class="topbar-user">
          <span class="topbar-bell">
            <AppIcon name="bell" :size="19" />
            <span class="bell-dot" aria-hidden="true"></span>
          </span>
          <span class="topbar-account">
            <span class="topbar-avatar"><AppIcon name="user" :size="16" /></span>
            <span class="topbar-name">用户</span>
            <span class="topbar-caret" aria-hidden="true">▾</span>
          </span>
        </div>
      </div>

      <div class="hero-content-wrap">
        <div class="hero-content">
          <img
            class="hero-logo"
            :src="logoLightUrl"
            alt="MetCom"
            width="900"
            height="190"
          />

          <h1 class="hero-title">
            <span class="hero-line">多源气象再分析资料</span>
            <span class="hero-line">对流层参数精细反演平台</span>
          </h1>

          <p class="hero-desc">
            <span class="hero-desc-line">面向多源气象再分析资料，</span>
            <span class="hero-desc-line">提供对流层参数计算、标准产品与定制化反演服务。</span>
          </p>

          <div class="hero-actions">
            <RouterLink class="hero-btn hero-btn-primary" to="/online/download">
              开始在线计算
              <span class="hero-btn-arrow" aria-hidden="true">→</span>
            </RouterLink>
            <RouterLink class="hero-btn hero-btn-ghost" to="/standard">
              浏览标准产品
              <span class="hero-btn-arrow" aria-hidden="true">→</span>
            </RouterLink>
            <RouterLink class="hero-btn hero-btn-ghost" to="/custom">
              开始定制产品
              <span class="hero-btn-arrow" aria-hidden="true">→</span>
            </RouterLink>
          </div>
        </div>
      </div>
    </section>

    <!-- 核心能力：四张宽卡片 -->
    <section class="section">
      <div class="section-inner">
        <header class="section-head">
          <h2 class="section-title">核心能力</h2>
          <p class="section-desc">构建多源气象再分析资料的一体化处理流程</p>
        </header>

        <div class="cap-grid">
          <article
            v-for="item in capabilities"
            :key="item.index"
            class="cap-card"
            :class="[item.veil, item.tone]"
          >
            <div
              class="cap-media"
              :style="{ backgroundImage: `url(${item.image})` }"
              aria-hidden="true"
            ></div>
            <div class="cap-veil" aria-hidden="true"></div>

            <div class="cap-body">
              <div class="cap-eyebrow">
                <span class="cap-index">{{ item.index }}</span>
                <span class="cap-en">{{ item.en }}</span>
              </div>
              <h3 class="cap-title">{{ item.title }}</h3>
              <p class="cap-desc">{{ item.desc }}</p>

              <ul class="cap-tags">
                <li v-for="tag in item.tags" :key="tag" class="cap-tag">{{ tag }}</li>
              </ul>
            </div>

            <span class="cap-arrow" aria-hidden="true">→</span>
          </article>
        </div>
      </div>
    </section>

    <!-- 数据基础：数据源表 + 对流层参数单元，同一块白面里的两段 -->
    <section class="section section-tight">
      <div class="section-inner">
        <header class="data-head">
          <div>
            <h2 class="data-title">数据基础</h2>
            <p class="data-sub">多源气象再分析资料与对流层参数体系</p>
          </div>
          <p class="data-meta">5 DATASETS · 6 PARAMETERS</p>
        </header>

        <div class="data-panel">
          <div class="data-block">
            <h3 class="block-label">数据源</h3>
            <p class="block-note">平台支持的多源气象再分析资料与区域数值预报</p>

            <div class="source-table">
              <div class="source-head">
                <span>数据源</span>
                <span>数据类型</span>
                <span>时间尺度</span>
              </div>
              <ul class="source-list">
                <li v-for="source in sources" :key="source.name" class="source-row">
                  <span class="source-name">{{ source.name }}</span>
                  <span class="source-type">{{ source.type }}</span>
                  <span class="source-scale">{{ source.scale }}</span>
                </li>
              </ul>
            </div>
          </div>

          <div class="data-block">
            <h3 class="block-label">对流层参数</h3>
            <p class="block-note">平台统一计算与输出的核心对流层参数</p>

            <ul class="param-band">
              <li v-for="param in parameters" :key="param.key" class="param-cell">
                <span class="param-key">{{ param.key }}</span>
                <span class="param-name">{{ param.name }}</span>
                <span class="param-unit">{{ param.unit }}</span>
              </li>
            </ul>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>
