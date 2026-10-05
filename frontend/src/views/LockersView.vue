<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import api from '@/api'
import Button from '@/components/ds/Button.vue'
import Card from '@/components/ds/Card.vue'
import TraceRule from '@/components/ds/TraceRule.vue'
import session from '@/stores/session'
import { available, reason } from '@/stores/status'
import MaintenanceNotice from '@/components/MaintenanceNotice.vue'

const raw = ref([])
const entitlement = ref(null)
const sel = ref(null)
const busy = ref(false)
const error = ref('')

async function load() {
  try {
    const data = await api.get('/lockers')
    raw.value = data.banks
    entitlement.value = data.entitlement
  } catch (e) {
    error.value = e.message
  }
}

onMounted(load)
// The map is per-user (your own locker reads differently from someone else's),
// so it has to be refetched when the session changes.
watch(() => session.authenticated, load)

const banks = computed(() =>
  raw.value.map((bank) => ({
    letter: bank.letter,
    note: bank.note,
    lockers: bank.lockers.map((l) => ({
      ...l,
      selected: sel.value === l.id,
      taken: l.state === 'taken' || l.state === 'inactive',
      mine: l.state === 'mine',
      free: l.state === 'free' && sel.value !== l.id,
    })),
  })),
)

const mine = computed(() =>
  raw.value.flatMap((b) => b.lockers).filter((l) => l.state === 'mine'),
)

function pick(l) {
  if (l.taken) return
  sel.value = sel.value === l.id ? null : l.id
}

const bookingEyebrow = computed(() => {
  if (!session.authenticated) return 'Not logged in'
  if (!session.isMember) return 'Members only'
  return sel.value ? 'Selected' : 'No locker selected'
})

const bookingTitle = computed(() => {
  if (!session.authenticated) return 'Log in to book a locker'
  if (!session.isMember) return 'Lockers are for Union members'
  return sel.value ? `Locker ${sel.value}, Level 4` : 'Pick a locker from the map'
})

const bookingNote = computed(() => {
  if (error.value) return error.value
  if (!session.authenticated) return 'Use your Imperial email — we send you a code.'
  if (!session.isMember) return 'Join EESoc through the Union shop, then come back.'
  if (entitlement.value && entitlement.value.remaining < 1) {
    return entitlement.value.purchased > 0
      ? `You are using all ${entitlement.value.purchased} locker(s) you have paid for.`
      : 'Buy a locker on the Union shop. Purchases reach us the following morning.'
  }
  if (sel.value) return 'Free for the year. Key collection Tuesdays, EEE 403.'
  return `Available lockers are outlined in blue. You have ${entitlement.value?.remaining ?? 0} left to claim.`
})

const canBook = computed(
  () => Boolean(sel.value) && session.isMember && (entitlement.value?.remaining ?? 0) > 0,
)

const bookLabel = computed(() => {
  if (busy.value) return 'Booking…'
  return sel.value ? `Book ${sel.value}` : 'Book locker'
})

async function book() {
  const target = raw.value.flatMap((b) => b.lockers).find((l) => l.id === sel.value)
  if (!target) return
  busy.value = true
  error.value = ''
  try {
    await api.post(`/lockers/${target.lockerId}/book`)
    sel.value = null
    await load()
  } catch (e) {
    error.value = e.message
    // Somebody else may have taken it while the panel was open.
    await load()
  } finally {
    busy.value = false
  }
}

async function release(locker) {
  busy.value = true
  try {
    const data = await api.get('/lockers/me')
    const booking = data.bookings.find((b) => b.locker === locker.id)
    if (booking) await api.post(`/lockers/bookings/${booking.id}/release`)
    await load()
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <!-- While booking is paused the whole page is the notice. Showing the bank
       map with a banner over it still reads as "pick one" — which is what it
       looked like — and the API would refuse anyway. -->
  <main v-if="!available('lockers')" class="lockers-paused">
    <span class="eyebrow">Level 4 · EEE building</span>
    <h1 style="margin:0; font:var(--type-h1); letter-spacing:var(--tracking-display); color:var(--text-strong)">Lockers</h1>
    <MaintenanceNotice
      :reason="reason('lockers')"
      title="Locker booking is not open yet"
    />
    <p style="margin:0; font:var(--type-body-long); color:var(--text-body)">
      EESoc lockers are on Level 4 of the EEE building and run for the academic
      year. They are bought through the Union shop — please don’t buy one yet,
      we’ll announce here as soon as booking opens.
    </p>
    <p v-if="mine.length" style="margin:0; font:var(--type-small); color:var(--text-body)">
      You currently hold {{ mine.map((l) => l.id).join(', ') }}.
      <Button size="sm" variant="ghost" :disabled="busy" @click="release(mine[0])">Release</Button>
    </p>
  </main>

  <main v-else class="lockers-layout">

    <div class="lockers-intro">
      <span class="eyebrow">Level 4 · EEE building</span>
      <h1 style="margin:0; font:var(--type-h1); letter-spacing:var(--tracking-display); color:var(--text-strong)">Lockers</h1>
      <p style="margin:0; font:var(--type-body-long); color:var(--text-body)">
        Lockers run for the academic year and are bought through the Union shop. Bring your own padlock; anything left after the last exam day is cut off and cleared.
      </p>
      <div style="display:grid; gap:var(--space-4)">
        <div class="step">
          <span class="step__n">01</span>
          <span style="font:var(--type-small); color:var(--text-body)">Log in with your Imperial email — we send a code. Lockers are for Union members.</span>
        </div>
        <div class="step">
          <span class="step__n">02</span>
          <span style="font:var(--type-small); color:var(--text-body)">Buy a locker on the Union shop, then pick any available one on the map.</span>
        </div>
        <div class="step">
          <span class="step__n">03</span>
          <span style="font:var(--type-small); color:var(--text-body)">Use it with your own padlock — there are no keys. If a locker already has a lock on it, contact us.</span>
        </div>
      </div>
      <TraceRule />
      <div class="legend">
        <span class="legend__item"><span class="legend__swatch legend__swatch--free"></span>Available</span>
        <span class="legend__item"><span class="legend__swatch legend__swatch--sel"></span>Selected</span>
        <span class="legend__item"><span class="legend__swatch legend__swatch--sel"></span>Yours</span>
        <span class="legend__item"><span class="legend__swatch legend__swatch--taken"></span>Taken</span>
      </div>
    </div>

    <div style="display:grid; gap:var(--space-8)">
      <div v-for="b in banks" :key="b.letter" style="display:grid; gap:var(--space-4)">
        <div style="display:flex; align-items:baseline; justify-content:space-between; gap:var(--space-6)">
          <h2 style="margin:0; font-family:var(--font-display); font-weight:700; font-size:var(--text-lg); letter-spacing:var(--tracking-heading); color:var(--text-strong)">Bank {{ b.letter }}</h2>
          <span class="eyebrow" style="letter-spacing:var(--tracking-label); color:var(--text-faint)">{{ b.note }}</span>
        </div>
        <div class="locker-grid">
          <template v-for="l in b.lockers" :key="l.id">
            <Button v-if="l.selected" variant="primary" block @click="pick(l)">{{ l.id }}</Button>
            <Button v-else-if="l.mine" variant="accent" block :title="'Yours — click to release'" @click="release(l)">{{ l.id }}</Button>
            <Button v-else-if="l.free" variant="secondary" block @click="pick(l)">{{ l.id }}</Button>
            <Button v-else variant="ghost" block disabled>{{ l.id }}</Button>
          </template>
        </div>
      </div>

      <Card variant="offset">
        <!-- The panel is replaced outright, not just the button disabled: the
             map above still shows which lockers are free, and a live-looking
             "Book this locker" that cannot work reads as a broken site rather
             than a deliberate pause. -->
        <div class="booking">
          <div style="display:grid; gap:6px">
            <span class="eyebrow" style="letter-spacing:var(--tracking-label)">{{ bookingEyebrow }}</span>
            <span style="font-family:var(--font-display); font-weight:700; font-size:var(--text-xl); letter-spacing:var(--tracking-heading); color:var(--text-strong)">{{ bookingTitle }}</span>
            <span style="font:var(--type-small); color:var(--text-muted)">{{ bookingNote }}</span>
          </div>
          <Button variant="accent" size="lg" :disabled="!canBook || busy" @click="book">{{ bookLabel }}</Button>
        </div>
      </Card>
    </div>
  </main>
</template>

<style scoped>
.eyebrow {
  font: var(--type-eyebrow);
  letter-spacing: var(--tracking-eyebrow);
  text-transform: uppercase;
  color: var(--text-accent);
}
.lockers-layout {
  max-width: var(--container-xl);
  margin: 0 auto;
  padding: var(--space-16) var(--space-8) var(--space-24);
  display: grid;
  grid-template-columns: 380px minmax(0, 1fr);
  gap: var(--space-16);
  align-items: start;
}
.lockers-paused {
  max-width: 62ch;
  margin: 0 auto;
  padding: var(--space-12) var(--space-6);
  display: grid;
  gap: var(--space-5);
}

.lockers-intro {
  display: grid;
  gap: var(--space-6);
  position: sticky;
  top: 96px;
}
.step {
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr);
  gap: var(--space-4);
  align-items: start;
}
.step__n {
  font-family: var(--font-display);
  font-weight: 700;
  color: var(--text-brand);
}
.legend {
  display: flex;
  gap: var(--space-6);
  flex-wrap: wrap;
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  letter-spacing: var(--tracking-label);
  text-transform: uppercase;
  color: var(--text-muted);
}
.legend__item {
  display: flex;
  align-items: center;
  gap: 8px;
}
.legend__swatch {
  width: 12px;
  height: 12px;
  border-radius: 2px;
}
.legend__swatch--free {
  border: 2px solid var(--border-brand);
}
.legend__swatch--sel {
  background: var(--surface-brand);
}
.legend__swatch--taken {
  background: var(--surface-sunken);
  border: 1px solid var(--border-default);
}
.locker-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: var(--space-3);
}
.booking {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: var(--space-8);
  align-items: center;
}
</style>
