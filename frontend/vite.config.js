import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// The build-variant tag exactly as the environment passed it. Production and
// CI builds never set it, so the empty string is the honest representation
// of "no variant"; every branch below keys off this raw value rather than
// the defaulted one.
const variantArg = process.env.EESOC_BUILD_VARIANT || ''

// Baked into the bundle by the `define` below (and stamped onto <html> by
// src/main.js), with a 'default' fallback so a live tab can always say which
// build it is running. The define only changes the content of the ENTRY
// chunk — main.js is part of it — which alone is not enough to tell two
// variants of the same tree apart, see the filename suffix below.
const buildVariant = variantArg || 'default'

// The variant also has to reach the emitted FILENAMES, and content hashing
// alone cannot do that: the lazy route chunks (see src/router/index.js) have
// byte-identical content across variants because only main.js mentions the
// variant, so their [hash] — and therefore their emitted names — would be
// identical, and the same-named chunk would exist in both bundles. That is
// precisely the "old chunk is still fetchable" case instead of a missing
// one, so a missing-chunk scenario could never be built or tested.
// Appending the variant to every emitted name makes two variant builds fully
// disjoint — no URL emitted by one exists in the other — which is what lets
// the asset-continuity e2e suite build two bundles from one source tree
// without editing anything in src/. The sanitizer restricts the suffix to
// filename-safe characters [A-Za-z0-9-].
const variantSuffix = variantArg.replace(/[^A-Za-z0-9-]/g, '-')

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
  // Applied ONLY when a variant was actually passed. With no variant this
  // whole key is absent and Vite's own defaults apply unchanged
  // (assets/[name]-[hash].js etc.), so production builds keep byte-for-byte
  // today's filenames and a variant suffix can never reach the cluster. The
  // assets/ prefix is kept in the overrides because nginx keys the
  // immutable cache header off location /assets/ (see nginx.conf).
  ...(variantSuffix
    ? {
        build: {
          rollupOptions: {
            output: {
              entryFileNames: `assets/[name]-[hash]-${variantSuffix}.js`,
              chunkFileNames: `assets/[name]-[hash]-${variantSuffix}.js`,
              assetFileNames: `assets/[name]-[hash]-${variantSuffix}[extname]`,
            },
          },
        },
      }
    : {}),
})
