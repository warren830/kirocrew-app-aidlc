import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// KiroCrew App bundle: a single ESM file whose DEFAULT export is the React component AppHost renders.
// Every runtime dependency below is provided by the host import map (see docs/research/03-…md §1):
// react, react-dom, react/jsx-runtime, @kirocrew/app-sdk, @kirocrew/app-sdk/ui, lucide-react.
export default defineConfig({
  plugins: [react()],
  build: {
    lib: { entry: 'src/main.tsx', formats: ['es'], fileName: () => 'index.mjs' },
    outDir: 'dist',
    emptyOutDir: false,
    cssCodeSplit: false,
    sourcemap: false,
    target: 'es2022',
    rollupOptions: {
      external: ['react', 'react-dom', 'react-dom/client', 'react/jsx-runtime', '@kirocrew/app-sdk', '@kirocrew/app-sdk/ui', 'lucide-react'],
      output: { assetFileNames: 'style[extname]' },
    },
  },
})
