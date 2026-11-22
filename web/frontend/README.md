# MetCom Web 前端

Vue 3 + Vite，无 UI 组件库、无状态管理库、无图表库；接口调用统一走 `src/api/client.js`。

```bat
npm install
npm run dev      :: http://127.0.0.1:5173，/api 代理到 127.0.0.1:8000
npm run build
```

- 目录职责、启动方式、页面结构与视觉规范见 `../README.md`。
- 全局样式集中在 `src/assets/styles.css`，只放设计令牌与通用类，不要在页面里写死颜色。
- 顶部导航与路由共用 `src/router/nav.js` 这一份清单。
