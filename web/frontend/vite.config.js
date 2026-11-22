import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// MetCom Web 前端开发配置。
// /api 统一代理到本地 FastAPI（127.0.0.1:8000），前端代码里只写 /api/xxx，
// 部署到服务器时由反向代理接管，前端不需要改代码。
export default defineConfig({
  plugins: [vue()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})