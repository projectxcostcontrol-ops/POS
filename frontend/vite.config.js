import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      input: {
        app: 'index.html',
        hongDuckMenu: 'menu/hong-duck/index.html',
        hongDuckQr: 'q/hong-duck/index.html',
      },
    },
  },
})
