/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // The backend has no /api prefix, so proxy its routes directly in dev.
    proxy: {
      '/editions': process.env.BACKEND_URL ?? 'http://localhost:8000',
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: './src/setupTests.ts',
  },
})
