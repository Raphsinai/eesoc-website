<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import api from '@/api'
import Button from '@/components/ds/Button.vue'
import Card from '@/components/ds/Card.vue'
import TraceRule from '@/components/ds/TraceRule.vue'
import session from '@/stores/session'
import { available, reason } from '@/stores/status'
import MaintenanceNotice from '@/components/MaintenanceNotice.vue'

// Official Imperial College Union product for EEE locker purchases.
const LOCKER_SHOP_URL = 'https://www-s.imperialcollegeunion.org/shop/product/2771-/'

const raw = ref([])
const entitlement = ref(null)
const sel = ref(null)
const busy = ref(false)
const error = ref('')

async function load() {
  try {
    const data = await api.get('/lockers')
    raw.value = data.floors
    entitlement.value = data.entitlement
  } catch (e) {
    error.value = e.message
  }
}

onMounted(load)
// The map is per-user (your own locker reads differently from someone else's),
// so it has to be refetched when the session changes.
watch(() => session.authenticated, load)

const all = computed(() =>
  raw.value.flatMap((f) => f.walls.flatMap((w) => w.blocks.flatMap((b) => b.lockers))),
)
const selected = computed(() => all.value.find((l) => l.id === sel.value) || null)
const mine = computed(() => all.value.filter((l) => l.state === 'mine'))
const anyInactive = computed(() => all.value.some((l) => l.state === 'inactive'))

// Every block is drawn at the width a block takes on the longest wall, so a
// block is the same size wherever it stands — as it is in the building — and a
// shorter wall simply ends sooner.
const widest = computed(() =>
  Math.max(1, ...raw.value.flatMap((f) => f.walls.map((w) => w.blocks.length))),
)

function look(l) {
  return sel.value === l.id ? 'selected' : l.state
}

const SAY = { free: 'available', taken: 'taken', mine: 'yours', inactive: 'out of service' }

function describe(l) {
  return `Locker ${l.number}, ${SAY[l.state] || l.state}. ${l.place}.`
}

function pick(l) {
  if (l.state === 'mine') return release(l)
  if (l.state !== 'free') return
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
  return selected.value ? `Locker ${selected.value.number}` : 'Pick a locker from the map'
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
  // Where it is, not just its number: some numbers appear on two blocks.
  if (selected.value) return `${selected.value.place}. Yours for the academic year.`
  return `Available lockers are outlined in blue. You have ${entitlement.value?.remaining ?? 0} left to claim.`
})

const needsPurchase = computed(
  () => session.authenticated && session.isMember
    && entitlement.value && entitlement.value.remaining < 1,
)

const canBook = computed(
  () => Boolean(selected.value) && session.isMember && (entitlement.value?.remaining ?? 0) > 0,
)

const bookLabel = computed(() => {
  if (busy.value) return 'Booking…'
  return selected.value ? `Book ${selected.value.number}` : 'Book locker'
})

async function book() {
  const target = selected.value
  if (!target) return
  busy.value = true
  error.value = ''
  try {
    await api.post(`/lockers/${target.id}/book`)
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
  // One tap on your own locker used to give it up outright. It is paid for,
  // and the moment it is released anyone can take it.
  if (!confirm(
    `Give up locker ${locker.number}? Anyone can book it straight away. `
    + 'Your purchase stays yours, so you can pick another.',
  )) return
  busy.value = true
  try {
    const data = await api.get('/lockers/me')
    const booking = data.bookings.find((b) => b.lockerId === locker.id)
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
    <span class="eyebrow">Levels 3–5 · EEE building</span>
    <h1 style="margin:0; font:var(--type-h1); letter-spacing:var(--tracking-display); color:var(--text-strong)">Lockers</h1>
    <MaintenanceNotice
      :reason="reason('lockers')"
      title="Locker booking is not open yet"
    />
    <p style="margin:0; font:var(--type-body-long); color:var(--text-body)">
      EESoc lockers are bought through the Union shop — please don’t buy one
      yet, we’ll announce here as soon as booking opens.
    </p>
    <p v-for="l in mine" :key="l.id" style="margin:0; font:var(--type-small); color:var(--text-body)">
      You hold locker {{ l.number }} — {{ l.place }}.
      <Button size="sm" variant="ghost" :disabled="busy" @click="release(l)">Release</Button>
    </p>
  </main>

  <main v-else class="lockers-layout">

    <div class="lockers-intro">
      <span class="eyebrow">Levels 3–5 · EEE building</span>
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
          <span style="font:var(--type-small); color:var(--text-body)">
            Buy the <a class="union-shop-link" :href="LOCKER_SHOP_URL" target="_blank" rel="noopener noreferrer">EEE locker on the Union Shop ↗</a>, then pick any available one on the map.
          </span>
        </div>
        <div class="step">
          <span class="step__n">03</span>
          <span style="font:var(--type-small); color:var(--text-body)">Use it with your own padlock — there are no keys. If a locker already has a lock on it, contact us.</span>
        </div>
      </div>
      <TraceRule />
      <p style="margin:0; font:var(--type-small); color:var(--text-body)">
        The map is drawn the way the lockers stand: blocks of nine, left to right
        along each wall, with the number on each door.
      </p>
      <div class="legend">
        <span class="legend__item"><span class="legend__swatch legend__swatch--free"></span>Available</span>
        <span class="legend__item"><span class="legend__swatch legend__swatch--sel"></span>Selected</span>
        <span class="legend__item"><span class="legend__swatch legend__swatch--mine"></span>Yours</span>
        <span class="legend__item"><span class="legend__swatch legend__swatch--taken"></span>Taken</span>
        <span v-if="anyInactive" class="legend__item"><span class="legend__swatch legend__swatch--inactive"></span>Out of service</span>
      </div>
    </div>

    <div class="locker-map" :style="{ '--blocks': widest }">
      <section v-for="f in raw" :key="f.floor" class="locker-floor">
        <div style="display:flex; align-items:baseline; justify-content:space-between; gap:var(--space-6)">
          <h2 style="margin:0; font-family:var(--font-display); font-weight:700; font-size:var(--text-lg); letter-spacing:var(--tracking-heading); color:var(--text-strong)">Level {{ f.floor }}</h2>
          <span class="eyebrow" style="letter-spacing:var(--tracking-label); color:var(--text-faint)">{{ f.note }}</span>
        </div>
        <div v-for="w in f.walls" :key="w.wall" class="locker-wall">
          <span class="locker-wall__label">{{ w.label }}</span>
          <div class="locker-wall__blocks">
            <div
              v-for="b in w.blocks"
              :key="b.block"
              class="locker-block"
              role="group"
              :aria-label="`Level ${f.floor}, ${w.label.toLowerCase()}, block ${b.block} from the left`"
            >
              <button
                v-for="l in b.lockers"
                :key="l.id"
                type="button"
                :class="['locker', `locker--${look(l)}`]"
                :style="{ gridRow: l.row, gridColumn: l.col }"
                :disabled="busy || l.state === 'taken' || l.state === 'inactive'"
                :aria-pressed="sel === l.id"
                :aria-label="describe(l)"
                :title="l.state === 'mine' ? `Yours — ${l.place}. Click to give it up.` : l.place"
                @click="pick(l)"
              >{{ l.number }}</button>
            </div>
          </div>
        </div>
      </section>

      <Card variant="offset" :class="{ 'booking-card--held': selected }">
        <!-- The panel is replaced outright, not just the button disabled: the
             map above still shows which lockers are free, and a live-looking
             "Book this locker" that cannot work reads as a broken site rather
             than a deliberate pause. -->
        <div class="booking">
          <div style="display:grid; gap:6px">
            <span class="eyebrow" style="letter-spacing:var(--tracking-label)">{{ bookingEyebrow }}</span>
            <span style="font-family:var(--font-display); font-weight:700; font-size:var(--text-xl); letter-spacing:var(--tracking-heading); color:var(--text-strong)">{{ bookingTitle }}</span>
            <span style="font:var(--type-small); color:var(--text-muted)">{{ bookingNote }}</span>
            <a v-if="needsPurchase" class="union-shop-link" :href="LOCKER_SHOP_URL" target="_blank" rel="noopener noreferrer">Buy a locker on the Union Shop ↗</a>
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
.union-shop-link {
  color: var(--text-accent);
  text-decoration: underline;
  text-underline-offset: 3px;
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
.legend__swatch--mine {
  background: var(--action-accent);
  border: 1px solid var(--ink-900);
}
.legend__swatch--taken {
  background: var(--surface-sunken);
  border: 1px solid var(--border-default);
}
.legend__swatch--inactive {
  background: repeating-linear-gradient(135deg, var(--surface-sunken) 0 2px, var(--border-default) 2px 3px);
  border: 1px solid var(--border-default);
}

/* --blocks (set from the data) is the most blocks on any one wall. A block
   takes its share of the width of that wall, so every block is the same size.
   Too narrow for the doors to stay legible, a wall scrolls sideways rather than
   wrapping — wrapping would put a block under its left-hand neighbour, which is
   not where it stands. */
.locker-map {
  --block-gap: var(--space-3);
  --door-gap: var(--space-1);
  --door-min: 28px;
  display: grid;
  gap: var(--space-10);
}
.locker-floor {
  display: grid;
  gap: var(--space-4);
}
.locker-wall {
  display: grid;
  gap: var(--space-2);
}
.locker-wall__label {
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  letter-spacing: var(--tracking-label);
  text-transform: uppercase;
  color: var(--text-muted);
}
.locker-wall__blocks {
  display: flex;
  gap: var(--block-gap);
  overflow-x: auto;
  /* Room for the focus ring, which the scroll container would otherwise clip. */
  padding: 3px;
  margin: -3px;
}
.locker-block {
  flex: none;
  width: calc((100% - (var(--blocks) - 1) * var(--block-gap)) / var(--blocks));
  min-width: calc(3 * var(--door-min) + 2 * var(--door-gap));
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--door-gap);
}
.locker {
  min-width: 0;
  height: 34px;
  padding: 0;
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
  border: var(--border-width) solid transparent;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: var(--transition-control);
}
.locker:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 1px;
}
.locker--free {
  background: transparent;
  color: var(--text-brand);
  border-color: var(--border-brand);
}
.locker--free:hover:not(:disabled) {
  background: var(--surface-brand-subtle);
}
.locker--selected {
  background: var(--action-primary);
  color: var(--text-on-brand);
  border-color: var(--action-primary);
}
.locker--mine {
  background: var(--action-accent);
  color: var(--ink-900);
  border-color: var(--ink-900);
}
.locker--mine:hover:not(:disabled) {
  background: var(--action-accent-hover);
}
.locker--taken,
.locker--inactive {
  background: var(--surface-sunken);
  color: var(--text-faint);
  border-color: transparent;
  cursor: not-allowed;
}
.locker--inactive {
  text-decoration: line-through;
}
.locker:disabled:not(.locker--taken):not(.locker--inactive) {
  cursor: progress;
}

/* Once a locker is picked, the panel stays in view while you scroll, so one
   chosen on level 3 can be booked without hunting for the button below level 5.
   Before that it sits at the end of the map rather than covering it. */
.booking-card--held {
  position: sticky;
  bottom: var(--space-4);
}
.booking {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: var(--space-8);
  align-items: center;
}
@media (max-width: 720px) {
  .booking {
    grid-template-columns: minmax(0, 1fr);
    gap: var(--space-4);
  }
}
</style>
