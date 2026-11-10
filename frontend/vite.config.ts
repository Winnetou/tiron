/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const backendUrl = process.env.BACKEND_URL ?? 'http://localhost:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // The backend has no /api prefix, so proxy its routes directly in dev.
    proxy: {
      '/editions': backendUrl,
      '/tei': backendUrl,
      '/ner': backendUrl,
      '/spellcheck': backendUrl,
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: './src/setupTests.ts',
    css: true, // so tests can check the styles in index.css
  },
})
