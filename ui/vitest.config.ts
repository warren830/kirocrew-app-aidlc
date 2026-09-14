import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'

/**
 * The three host-provided specifiers have no npm package — the dashboard supplies them at runtime
 * through its import map — so tests alias them to behavioural stubs under src/test/stubs. Aliasing is
 * what makes a component test possible at all; without it every import of a Studio component fails to
 * resolve.
 */
const stub = (name: string) => fileURLToPath(new URL(`./src/test/stubs/${name}`, import.meta.url))

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: [
      { find: '@kirocrew/app-sdk/ui', replacement: stub('app-sdk-ui.tsx') },
      { find: '@kirocrew/app-sdk', replacement: stub('app-sdk.tsx') },
      { find: 'lucide-react', replacement: stub('lucide.tsx') },
    ],
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
    setupFiles: ['./src/test/setup.ts'],
    globals: false,
    restoreMocks: true,
  },
})
