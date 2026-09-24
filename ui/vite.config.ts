/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    // Relative fetch('/api/shutdown') from the UI hits Vite; proxy to the mock/backend.
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
        // Mark proxied requests so the server never mistakes a phone using the
        // dev server for the kiosk itself (/api/system is local-device only;
        // the Connections UI calls the server origin directly).
        xfwd: true,
      },
    },
  },
  test: {
    include: ['src/**/*.test.{ts,tsx}', 'tests/**/*.test.{ts,tsx}'],
    exclude: ['tests/e2e/**'],
  },
});
