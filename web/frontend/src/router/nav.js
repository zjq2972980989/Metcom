// 左侧导航单一数据源：路由与 Sidebar 都从这里读，避免两处各写一份。
// 顺序即锁定顺序：首页 / 在线计算 / 标准产品 / 定制产品 / 任务中心 / 关于平台
// icon 取 AppIcon.vue 里已有的图标名，不新造图形。
export const NAV_ITEMS = [
  { key: 'home', label: '首页', to: '/', icon: 'home' },
  {
    key: 'online',
    label: '在线计算',
    to: '/online/download',
    icon: 'download-cloud',
    children: [
      { key: 'download', label: '数据下载', to: '/online/download' },
      { key: 'calculation', label: '参数计算', to: '/online/calculation' },
      { key: 'timeinterp', label: '时间插值', to: '/online/timeinterp' },
    ],
  },
  { key: 'standard', label: '标准产品', to: '/standard', icon: 'file-lines' },
  { key: 'custom', label: '定制产品', to: '/custom', icon: 'target' },
  { key: 'tasks', label: '任务中心', to: '/tasks', icon: 'list' },
  { key: 'about', label: '关于平台', to: '/about', icon: 'info' },
]
