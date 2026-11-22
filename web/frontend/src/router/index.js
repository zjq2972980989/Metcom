import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    name: 'home',
    component: () => import('../views/Home.vue'),
    meta: { title: '首页', nav: 'home' },
  },
  {
    path: '/online',
    component: () => import('../views/OnlineCalculation.vue'),
    redirect: '/online/download',
    meta: { nav: 'online' },
    children: [
      {
        path: 'download',
        name: 'download',
        component: () => import('../views/DataDownload.vue'),
        meta: { title: '数据下载', nav: 'online' },
      },
      {
        path: 'calculation',
        name: 'calculation',
        component: () => import('../views/ParameterCalculation.vue'),
        meta: { title: '参数计算', nav: 'online' },
      },
      {
        path: 'timeinterp',
        name: 'timeinterp',
        component: () => import('../views/TimeInterp.vue'),
        meta: { title: '时间插值', nav: 'online' },
      },
    ],
  },
  {
    path: '/standard',
    name: 'standard',
    component: () => import('../views/StandardProducts.vue'),
    meta: { title: '标准产品', nav: 'standard' },
  },
  {
    path: '/custom',
    name: 'custom',
    component: () => import('../views/CustomProducts.vue'),
    meta: { title: '定制产品', nav: 'custom' },
  },
  {
    path: '/tasks',
    name: 'tasks',
    component: () => import('../views/TaskCenter.vue'),
    meta: { title: '任务中心', nav: 'tasks' },
  },
  {
    path: '/preview/:taskId',
    name: 'preview',
    component: () => import('../views/ResultPreview.vue'),
    props: true,
    meta: { title: '结果预览', nav: 'tasks' },
  },
  {
    path: '/about',
    name: 'about',
    component: () => import('../views/About.vue'),
    meta: { title: '关于平台', nav: 'about' },
  },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

router.afterEach((to) => {
  const title = to.meta?.title
  document.title = title ? `${title} · MetCom` : 'MetCom'
})

export default router
