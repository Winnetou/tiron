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
      // The address of a page in the browser is the endpoint's path too:
      // the browser asking for HTML gets the app, everything else the backend.
      '/editions': {
        target: backendUrl,
        bypass: (request) =>
          request.headers.accept?.includes('text/html') ? '/index.html' : undefined,
      },
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
