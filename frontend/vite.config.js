import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Lets CI/tests vary the bundle content (and therefore the emitted JS
// hashes) without touching source files: building the same tree twice with
// two different variants produces a distinct entry chunk, which the
// asset-continuity e2e suite uses to simulate a deploy. The define only
// reaches JS, so a variant change alone does not re-hash the CSS.
const buildVariant = process.env.EESOC_BUILD_VARIANT || 'default'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  define: {
    __BUILD_VARIANT__: JSON.stringify(buildVariant),
  },
})
