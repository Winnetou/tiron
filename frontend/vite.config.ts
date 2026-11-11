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
    testTimeout: 15000, // some tests drive many clicks; do not flake on a busy machine
    css: true, // so tests can check the styles in index.css
  },
})
