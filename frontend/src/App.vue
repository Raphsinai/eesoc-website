<script setup>
import { onMounted, provide, ref } from 'vue'

import SiteHeader from '@/components/SiteHeader.vue'
import SiteFooter from '@/components/SiteFooter.vue'
import LoginDialog from '@/components/LoginDialog.vue'
import session, { refresh, stopImpersonating } from '@/stores/session'
import MaintenanceNotice from '@/components/MaintenanceNotice.vue'
import status, { refreshStatus } from '@/stores/status'

const loginOpen = ref(false)
const leaving = ref(false)

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

// Stopping lands you back on your own account. Reload rather than just
// refreshing the store: every view on screen was rendered for the other
// person, and leaving half of it up is how you end up acting on their data
// believing it is yours.
async function stopPretending() {
  leaving.value = true
  try {
    await stopImpersonating()
    window.location.assign('/')
  } catch {
    leaving.value = false
  }
}
</script>

<template>
  <!-- Deliberately above the header, on every page, and with no way to dismiss
       it: the one failure mode of impersonation is forgetting you are doing
       it, and a banner you can close is a banner you will close. -->
  <div v-if="session.impersonating" class="impersonating">
    <span>
      Signed in as <strong>{{ session.user?.email }}</strong> —
      you are {{ session.impersonatorEmail }}.
      Anything you do here is done as them.
    </span>
    <button type="button" :disabled="leaving" @click="stopPretending">
      {{ leaving ? 'Returning…' : 'Stop' }}
    </button>
  </div>

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
.impersonating {
  position: sticky;
  top: 0;
  z-index: 100;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--gutter);
  background: var(--danger, #b3261e);
  color: #fff;
  font: var(--type-small);
  text-align: center;
}

.impersonating button {
  border: 1px solid currentColor;
  border-radius: var(--radius-sm, 4px);
  background: transparent;
  color: inherit;
  padding: 2px 10px;
  font: inherit;
  cursor: pointer;
}

.impersonating button:disabled {
  opacity: 0.6;
  cursor: default;
}

.site-maint {
  max-width: var(--container-xl);
  margin: var(--space-4) auto 0;
  padding: 0 var(--gutter);
}
</style>
