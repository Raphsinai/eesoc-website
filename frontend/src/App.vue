<script setup>
import { onMounted, provide, ref } from 'vue'

import SiteHeader from '@/components/SiteHeader.vue'
import SiteFooter from '@/components/SiteFooter.vue'
import LoginDialog from '@/components/LoginDialog.vue'
import { refresh } from '@/stores/session'
import MaintenanceNotice from '@/components/MaintenanceNotice.vue'
import status, { refreshStatus } from '@/stores/status'

const loginOpen = ref(false)

// Any view can ask for the login dialog without threading events up the tree
// (the booking panel on an event page needs it, for one).
provide('openLogin', () => {
  loginOpen.value = true
})

// One session probe on boot; every later change goes through the store.
// The feature-status probe rides along so the UI knows what is usable before
// anybody clicks it — the two are independent, so they run concurrently.
onMounted(() => {
  refresh()
  refreshStatus()
})
</script>

<template>
  <SiteHeader @open-login="loginOpen = true" />

  <!-- Site-wide only when more than one thing is down; a single unavailable
       feature says so at the control itself, where it is actually relevant. -->
  <div v-if="status.notice && status.unavailable.length > 1" class="site-maint">
    <MaintenanceNotice :reason="status.notice" inline />
  </div>

  <router-view @need-login="loginOpen = true" />

  <!-- Views swap instantly on navigation. The design system's motion is
       "switch-like — things toggle, they don't float", so no cross-fade. -->

  <SiteFooter />

  <LoginDialog v-model:open="loginOpen" />
</template>

<style scoped>
.site-maint {
  max-width: var(--container-xl);
  margin: var(--space-4) auto 0;
  padding: 0 var(--gutter);
}
</style>
