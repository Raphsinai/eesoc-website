<script setup>
// A ticket: the QR the door scans, and what it is for. Reached from the
// confirmation email, from the event page for your own booking, or by scanning
// the QR with any phone camera. The link is the credential, so no sign-in.
//
// A committee member who opens it (having scanned with their phone's own
// camera, say) gets a Check in button.
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { usePageTitle } from '@/utils/pageTitle'

import api from '@/api'
import Badge from '@/components/ds/Badge.vue'
import Button from '@/components/ds/Button.vue'
import Card from '@/components/ds/Card.vue'

const route = useRoute()
const ticket = ref(null)
const error = ref('')
const result = ref(null)
const busy = ref(false)

async function load() {
  try {
    ticket.value = await api.get(`/tickets/${route.params.token}`)
  } catch (e) {
    error.value = e.message
  }
}

async function checkIn() {
  busy.value = true
  try {
    result.value = await api.post('/checkin/scan', {
      token: route.params.token,
      event_id: ticket.value.event.id,
    })
    await load()
  } catch (e) {
    result.value = { outcome: 'error', message: e.message }
  } finally {
    busy.value = false
  }
}

async function letInAnyway() {
  busy.value = true
  try {
    const id = Number(String(route.params.token).split('.')[0])
    result.value = await api.post(`/checkin/reservations/${id}`, {
      event_id: ticket.value.event.id,
      override: true,
    })
    await load()
  } catch (e) {
    result.value = { outcome: 'error', message: e.message }
  } finally {
    busy.value = false
  }
}

const HEADLINE = {
  checked_in: 'Checked in',
  already: 'Already in',
  unpaid: 'Not paid',
  invalid: 'Not valid',
  wrong_event: 'Wrong event',
  error: 'Could not check in',
}

onMounted(load)
usePageTitle(computed(() => ticket.value?.event?.title ? `${ticket.value.event.title} · Your ticket` : 'Your ticket'))
</script>

<template>
  <main class="ticket-page">
    <p v-if="error" class="error">{{ error }}</p>
    <Card v-if="ticket" variant="offset" class="ticket">
      <div class="ticket__body">
        <span class="eyebrow">Your ticket</span>
        <h1 class="ticket__title">{{ ticket.event.title }}</h1>
        <p class="muted">
          {{ ticket.event.date }} · {{ ticket.event.time }}<template v-if="ticket.event.venue"> · {{ ticket.event.venue }}</template>
        </p>

        <div class="ticket__qr" :class="{ 'is-dim': !ticket.valid }">
          <img :src="ticket.qrUrl" alt="Ticket QR code" width="260" height="260" />
        </div>

        <div class="ticket__who">
          <strong>{{ ticket.holder }}</strong>
          <span>{{ ticket.ticketType }}<template v-if="ticket.quantity > 1"> × {{ ticket.quantity }}</template></span>
        </div>
        <div style="display:flex; gap:var(--space-2); justify-content:center; flex-wrap:wrap">
          <Badge :tone="ticket.valid ? 'success' : ticket.status === 'held' ? 'accent' : 'neutral'">{{ ticket.statusLabel }}</Badge>
          <Badge v-if="ticket.checkedInAt" tone="brand">
            Checked in {{ new Date(ticket.checkedInAt).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }) }}
          </Badge>
        </div>
        <p class="muted" style="text-align:center">
          <template v-if="ticket.valid">Show this at the door. Brightness up helps the scanner.</template>
          <template v-else-if="ticket.status === 'held'">This booking isn't paid yet, so the door can't let you in on it.</template>
          <template v-else>This booking is no longer valid.</template>
        </p>

        <!-- Committee only: the API refuses everyone else anyway. -->
        <div v-if="ticket.mayCheckIn" class="ticket__door">
          <span class="label">Committee</span>
          <div v-if="result" class="door-result" :class="`is-${result.outcome}`">
            <strong>{{ HEADLINE[result.outcome] || result.outcome }}</strong>
            <span v-if="result.message">{{ result.message }}</span>
          </div>
          <Button v-if="!ticket.checkedInAt" block :disabled="busy" @click="checkIn">Check in</Button>
          <Button
            v-if="result && (result.outcome === 'unpaid' || result.outcome === 'invalid')"
            block
            variant="secondary"
            :disabled="busy"
            @click="letInAnyway"
          >Let in anyway</Button>
          <RouterLink :to="`/checkin/${ticket.event.id}`" class="muted">Open the door screen for this event →</RouterLink>
        </div>
      </div>
    </Card>
  </main>
</template>

<style scoped>
.ticket-page {
  max-width: 460px;
  margin: 0 auto;
  padding: var(--space-8) var(--space-4) var(--space-24);
  min-height: 80vh;
}
.ticket__body {
  display: grid;
  gap: var(--space-4);
  justify-items: center;
  text-align: center;
}
.eyebrow {
  font: var(--type-eyebrow);
  letter-spacing: var(--tracking-eyebrow);
  text-transform: uppercase;
  color: var(--text-accent);
}
.label {
  font: var(--type-eyebrow);
  letter-spacing: var(--tracking-label);
  text-transform: uppercase;
  color: var(--text-muted);
}
.ticket__title {
  margin: 0;
  font: var(--type-h2);
  color: var(--text-strong);
}
.muted {
  margin: 0;
  font: var(--type-small);
  color: var(--text-muted);
}
.error {
  font: var(--type-small);
  color: var(--danger);
}
.ticket__qr {
  padding: var(--space-3);
  background: #fff;
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-md);
}
.ticket__qr img {
  display: block;
  width: 260px;
  height: 260px;
  image-rendering: pixelated;
}
.ticket__qr.is-dim {
  opacity: 0.35;
}
.ticket__who {
  display: grid;
  gap: 2px;
  font: var(--type-body);
}
.ticket__door {
  width: 100%;
  display: grid;
  gap: var(--space-3);
  padding-top: var(--space-4);
  border-top: 1px solid var(--border-hairline);
}
.door-result {
  display: grid;
  gap: 2px;
  padding: var(--space-3);
  border-radius: var(--radius-sm);
  color: #fff;
  background: #b02020;
}
.door-result.is-checked_in {
  background: #16803d;
}
.door-result.is-already {
  background: #c47800;
}
</style>
