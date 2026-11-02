import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { proxy: { '/health': 'http://localhost:8000', '/api': 'http://localhost:8000', '/auth': 'http://localhost:8000' } },
  test: { environment: 'jsdom', setupFiles: './src/test-setup.js' },
})
