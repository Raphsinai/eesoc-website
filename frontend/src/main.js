import { createApp } from 'vue'
import router from './router'
import App from './App.vue'
import { installChunkRecovery } from './chunk-recovery'
import './styles/app.css'

// Install before mounting: a tab that survives a deploy must have the
// chunk-load recovery hook in place before it can ever attempt a lazy
// navigation, otherwise the very first broken navigation would be fatal.
installChunkRecovery(router)

// Identifies which build a live tab is running — useful when debugging
// stale-tab reports. It also lets the e2e suite build two bundles with
// different hashes from one source tree (see vite.config.js).
document.documentElement.dataset.build = __BUILD_VARIANT__

createApp(App).use(router).mount('#app')
