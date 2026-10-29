import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: true,
    port: 5173,
    allowedHosts: true,
    // Forward /api requests to the FastAPI backend during development,
    // so the frontend can use relative URLs.
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
