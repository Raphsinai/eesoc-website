<script setup>
// One event, and the booking flow.
//
// Booking is a reservation, not a payment: the money changes hands on the Union
// shop and their sales feed only refreshes at 06:00. So the page's job after a
// hold is to tell the user exactly what happens next, which differs for members
// (matched automatically on their Union login) and guests (who type in an order
// number).
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import api from '@/api'
import Card from '@/components/ds/Card.vue'
import Button from '@/components/ds/Button.vue'
import Badge from '@/components/ds/Badge.vue'
import Field from '@/components/ds/Field.vue'
import Input from '@/components/ds/Input.vue'
import ImageSlot from '@/components/ds/ImageSlot.vue'
import TraceRule from '@/components/ds/TraceRule.vue'
import session from '@/stores/session'
import MaintenanceNotice from '@/components/MaintenanceNotice.vue'
import { available, reason } from '@/stores/status'

const route = useRoute()
const emit = defineEmits(['need-login'])

const event = ref(null)
const loading = ref(true)
const error = ref('')
const chosen = ref(null)
const quantity = ref(1)
const busy = ref(false)
const message = ref('')
const membershipWarning = ref('')
const orderNumbers = ref({})

// Guest booking. Signed-out visitors can hold a ticket; we just need to know
// who they are, and their shortcode if they are at Imperial (that is what lets
// us match their Union purchase without them chasing an order number).
const showGuestForm = ref(false)
const guest = ref({ email: '', first_name: '', last_name: '', shortcode: '' })
const guestIsImperial = computed(() => /@(ic|imperial)\.ac\.uk$/i.test(guest.value.email.trim()))

// The seat is held from the moment Reserve is pressed, so nobody can take it
// while the form is being filled in. The hold lasts minutes and is given back
// the instant the form is abandoned — it is not a booking.
const draftToken = ref(null)
const draftExpiresAt = ref(null)
const secondsLeft = ref(0)
let ticker = null

const countdown = computed(() => {
  const total = Math.max(secondsLeft.value, 0)
  const m = String(Math.floor(total / 60)).padStart(2, '0')
  const sec = String(total % 60).padStart(2, '0')
  return `${m}:${sec}`
})

function startTimer() {
  clearInterval(ticker)
  ticker = setInterval(() => {
    secondsLeft.value = Math.max(
      0,
      Math.round((new Date(draftExpiresAt.value) - Date.now()) / 1000),
    )
    if (secondsLeft.value <= 0) {
      clearInterval(ticker)
      // The server has already let the seat go; say so rather than letting them
      // submit into a failure.
      draftToken.value = null
      showGuestForm.value = false
      error.value = 'Your hold ran out and the ticket went back on sale.'
    }
  }, 1000)
}

async function releaseHold(useBeacon = false) {
  const token = draftToken.value
  if (!token) return
  draftToken.value = null
  clearInterval(ticker)
  const body = JSON.stringify({ draft_token: token })
  const url = `/api/events/${route.params.slug}/release-hold`
  if (useBeacon && navigator.sendBeacon) {
    // Closing the tab kills in-flight fetches; sendBeacon survives it, so the
    // seat is not stranded until the sweep.
    navigator.sendBeacon(url, new Blob([body], { type: 'application/json' }))
    return
  }
  try {
    await api.post(`/events/${route.params.slug}/release-hold`, { draft_token: token })
  } catch {
    // Best effort — the hold expires on its own shortly anyway.
  }
}

function abandonForm() {
  showGuestForm.value = false
  releaseHold()
}

function beforeUnload() {
  releaseHold(true)
}

onMounted(() => window.addEventListener('beforeunload', beforeUnload))
onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', beforeUnload)
  releaseHold(true)
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    event.value = await api.get(`/events/${route.params.slug}`)
    const first = (event.value.ticketTypes || []).find((t) => t.selectable)
    chosen.value = chosen.value ?? first?.id ?? null
  } catch (e) {
    error.value = e.status === 404 ? 'That event does not exist.' : e.message
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(() => route.params.slug, load)

const selected = computed(
  () => (event.value?.ticketTypes || []).find((t) => t.id === chosen.value) || null,
)

// Members-only tickets can still be booked when we cannot see a membership —
// somebody who joined today is not in the Union feed until the morning sync. The
// booking is taken and verified before it is confirmed, so the honest thing is
// to warn rather than to lock the option.
const needsMembership = computed(() => Boolean(selected.value?.needsMembership))

const maxQuantity = computed(() => {
  if (!selected.value) return 1
  const held = (event.value?.myReservations || [])
    .filter((r) => r.ticketTypeId === selected.value.id)
    .reduce((n, r) => n + r.quantity, 0)
  const perUser = Math.max(selected.value.maxPerUser - held, 0)
  const left = selected.value.remaining ?? perUser
  return Math.max(Math.min(perUser, left), 0)
})

const canBook = computed(() => {
  if (!selected.value?.selectable) return false
  // A guest's own bookings come back via their visitor cookie, so a second
  // Reserve for a ticket they already hold is refused here, not after the form.
  if (maxQuantity.value <= 0) return false
  if (!session.authenticated) {
    // The form has to be filled in before the second press does anything.
    if (!showGuestForm.value) return true
    if (!guest.value.email.includes('@')) return false
    if (guestIsImperial.value && !guest.value.shortcode.trim()) return false
    return true
  }
  return maxQuantity.value > 0
})

async function book() {
  // Not signed in: take the seat first, then collect their details. Doing it in
  // that order is the point — otherwise the last ticket can be sold to somebody
  // else while they are still typing.
  if (!session.authenticated && !showGuestForm.value) {
    busy.value = true
    error.value = ''
    try {
      const res = await api.post(`/events/${route.params.slug}/hold`, {
        ticket_type_id: chosen.value,
        quantity: quantity.value,
      })
      draftToken.value = res.draftToken
      draftExpiresAt.value = res.expiresAt
      secondsLeft.value = res.holdMinutes * 60
      showGuestForm.value = true
      startTimer()
      await load()
    } catch (e) {
      error.value = e.message
    } finally {
      busy.value = false
    }
    return
  }
  busy.value = true
  message.value = ''
  error.value = ''
  try {
    const payload = { ticket_type_id: chosen.value, quantity: quantity.value }
    if (!session.authenticated) {
      payload.guest = {
        email: guest.value.email.trim(),
        first_name: guest.value.first_name.trim() || null,
        last_name: guest.value.last_name.trim() || null,
        shortcode: guest.value.shortcode.trim() || null,
      }
    }
    if (draftToken.value) payload.draft_token = draftToken.value
    const res = await api.post(`/events/${route.params.slug}/reserve`, payload)
    message.value = res.next.message
    membershipWarning.value = res.membershipWarning || ''
    // Submitted: the hold has become a booking waiting on payment, so stop the
    // countdown rather than releasing anything.
    draftToken.value = null
    clearInterval(ticker)
    showGuestForm.value = false
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

async function cancel(reservation) {
  busy.value = true
  try {
    await api.post(`/reservations/${reservation.id}/cancel`)
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

async function submitOrder(reservation) {
  const value = (orderNumbers.value[reservation.id] || '').trim()
  if (!value) return
  busy.value = true
  error.value = ''
  try {
    const res = await api.patch(`/reservations/${reservation.id}/order-number`, {
      order_number: value,
    })
    message.value = res.message
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

const statusTone = { confirmed: 'success', held: 'accent', expired: 'neutral', cancelled: 'neutral' }

// "Tell me when this goes on sale".
//
// The only thing anybody can do on an event that is not yet bookable, so it
// stands in for the booking panel rather than sitting beside it. Open to
// visitors who are not signed in — requiring an account first would lose most
// of the people it exists for.
const alertEmail = ref('')
const alertDone = ref('')
const alertBusy = ref(false)

const canAlert = computed(() =>
  session.authenticated || alertEmail.value.trim().includes('@'),
)

async function notifyMe() {
  alertBusy.value = true
  error.value = ''
  try {
    const res = await api.post(`/events/${route.params.slug}/notify-me`, {
      email: session.authenticated ? null : alertEmail.value.trim(),
    })
    alertDone.value = res.message
    // Tickets turned out to be on sale after all — reload rather than leaving
    // them looking at a form for something they could just book.
    if (res.alreadyOpen) await load()
  } catch (e) {
    error.value = e.message
  } finally {
    alertBusy.value = false
  }
}

// Calendar and links. Plain anchors, not api() calls: both are GETs a browser
// opens directly, and the .ics endpoint is deliberately session-free so a
// calendar client can fetch the same URL.
const icsHref = computed(() => `/api/events/${route.params.slug}/calendar.ics`)

const gcalHref = computed(() => {
  // Google's template link wants both ends of the event. Without an end we
  // leave the link off rather than invent a duration — the .ics handles the
  // no-end case honestly, so there is still a calendar route in.
  if (!event.value?.startsAt || !event.value?.endsAt) return ''
  const stamp = (iso) =>
    new Date(iso).toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '')
  const params = new URLSearchParams({
    action: 'TEMPLATE',
    text: event.value.title || '',
    dates: `${stamp(event.value.startsAt)}/${stamp(event.value.endsAt)}`,
    details: event.value.description || '',
    location: event.value.venue || '',
  })
  return `https://calendar.google.com/calendar/render?${params}`
})

// "Aimed at: 2nd, 3rd years". An empty list means everyone, which is the
// default and not worth a line on the page.
const YEAR_NAMES = { 1: '1st', 2: '2nd', 3: '3rd', 4: '4th' }
const aimedAt = computed(() => {
  const years = event.value?.targetYears || []
  if (!years.length) return ''
  return years.map((y) => YEAR_NAMES[y] || `${y}th`).join(', ')
})
</script>


// Shown only when salesState is "scheduled", i.e. salesOpenAt is set and in
// the future. Rendered in the viewer's own timezone, which is what they will
// be going by.
const salesOpensText = computed(() => {
  if (!event.value?.salesOpenAt) return ''
  return new Date(event.value.salesOpenAt).toLocaleString(undefined, {
    weekday: 'short', day: '2-digit', month: 'short',
    hour: '2-digit', minute: '2-digit',
  })
})

<template>
  <main style="max-width:var(--container-lg); margin:0 auto; padding:var(--space-10) var(--space-6)">
    <p v-if="loading" style="font:var(--type-small); color:var(--text-faint)">Loading…</p>
    <p v-else-if="error && !event" style="font:var(--type-small); color:var(--danger)">{{ error }}</p>

    <template v-else-if="event">
      <router-link
        to="/events"
        style="font-family:var(--font-mono); font-size:var(--text-2xs); letter-spacing:var(--tracking-label); text-transform:uppercase; color:var(--text-faint); text-decoration:none"
      >
        ← All events
      </router-link>

      <div class="event-layout">
        <!-- Source order is image, title, description — which is exactly the
             order wanted on a phone, where this column simply stacks. No CSS
             `order` juggling, so the reading order and the DOM order agree
             (screen readers and tab order follow the DOM, not the visual
             grid). -->
        <div class="event-main">
          <div v-if="event.imageUrl" class="event-media">
            <img :src="event.imageUrl" :alt="event.title" class="event-image" />
          </div>
          <div v-else class="event-media event-media--empty">
            <ImageSlot label="Event image" />
          </div>

          <header style="display:grid; gap:var(--space-4)">
            <div style="display:flex; gap:var(--space-3); align-items:center; flex-wrap:wrap">
              <Badge tone="brand">{{ event.category }}</Badge>
              <Badge :tone="event.statusTone">{{ event.status }}</Badge>
              <Badge v-if="!event.published" tone="neutral">Draft</Badge>
            </div>
            <h1 style="margin:0; font:var(--type-h1)">{{ event.title }}</h1>
            <p style="margin:0; font:var(--type-small); color:var(--text-muted)">
              {{ event.date }} · {{ event.time }}<span v-if="event.venue"> · {{ event.venue }}</span>
            </p>
          </header>

          <p style="margin:0; font:var(--type-body); white-space:pre-line">{{ event.description }}</p>

          <!-- Calendar and links: the things somebody wants to do with an
               event that are not booking it. The .ics link is always shown —
               every published event has one — and the rest appear only when
               the API sent them. -->
          <div class="calendar-links">
            <h2 style="margin:0; font:var(--type-h5)">Calendar &amp; links</h2>
            <div class="calendar-links__row">
              <a data-testid="event-ics-link" :href="icsHref">Add to calendar (.ics) ↓</a>
              <a
                v-if="gcalHref"
                data-testid="event-gcal-link"
                :href="gcalHref"
                target="_blank"
                rel="noopener"
              >Google Calendar ↗</a>
              <a
                v-if="event.mapUrl"
                data-testid="event-map-link"
                :href="event.mapUrl"
                target="_blank"
                rel="noopener"
              >Venue map ↗</a>
              <a
                v-if="event.meetingUrl"
                data-testid="event-meeting-link"
                :href="event.meetingUrl"
                target="_blank"
                rel="noopener"
              >Join online ↗</a>
            </div>
            <!-- The API withheld the link because it is for booked attendees
                 only: say so rather than leaving nothing, which reads as
                 "there is no online option". -->
            <p
              v-if="event.meetingUrlPrivate && !event.meetingUrl"
              style="margin:0; font:var(--type-small); color:var(--text-faint)"
            >
              The joining link appears here once you've booked.
            </p>
            <p v-if="aimedAt" style="margin:0; font:var(--type-small); color:var(--text-muted)">
              Aimed at: {{ aimedAt }} years
            </p>
          </div>
        </div>

        <!-- No ticket types means no booking: a drop-in event gets a short
             note instead of a panel, rather than a "Book a place" card with
             nothing in it. -->
        <!-- Sign-ups on somebody else's form (usually a sponsor's): link out
             rather than show a booking panel that would not count. -->
        <Card v-if="event.signupUrl" variant="offset">
          <h2 style="margin:0 0 var(--space-3); font:var(--type-h4)">Sign up</h2>
          <p style="margin:0 0 var(--space-5); font:var(--type-small); color:var(--text-muted)">
            Places for this event are taken through its own sign-up form.
          </p>
          <Button block :href="event.signupUrl" target="_blank" rel="noopener">Sign up ↗</Button>
        </Card>

        <Card v-else-if="!event.ticketTypes?.length" variant="offset">
          <h2 style="margin:0 0 var(--space-3); font:var(--type-h4)">No booking needed</h2>
          <p style="margin:0; font:var(--type-small); color:var(--text-muted)">
            This one is free and open to all — just turn up.
          </p>
        </Card>

        <Card v-else variant="offset">
          <h2 style="margin:0 0 var(--space-5); font:var(--type-h4)">Book a place</h2>

          <!-- The whole panel goes, not just the button. Leaving the ticket
               picker up would let somebody choose a ticket and pick a quantity
               before finding out none of it can be submitted. -->
          <MaintenanceNotice
            v-if="!available('bookings')"
            :reason="reason('bookings')"
            title="Booking is unavailable"
          />

          <!-- Same reasoning as the maintenance case: the whole picker goes,
               rather than leaving a form that cannot be submitted. -->
          <div v-else-if="event.salesState !== 'open'" class="sales-notice">
            <p style="margin:0; font:var(--type-small); color:var(--text-strong)">
              {{ event.salesState === 'scheduled' ? 'Tickets go on sale' : 'On sale soon' }}
            </p>
            <p v-if="event.salesState === 'scheduled'" style="margin:var(--space-2) 0 0; font:var(--type-h5); color:var(--text-strong)">
              {{ salesOpensText }}
            </p>
            <p style="margin:var(--space-3) 0 0; font:var(--type-small); color:var(--text-muted)">
              {{
                event.salesState === 'scheduled'
                  ? 'Come back then, or let us email you the moment they open.'
                  : 'We haven’t set a date yet — let us email you when we do.'
              }}
            </p>

            <!-- The alternative to "come back and check", which nobody does. -->
            <div class="sales-alert">
              <p v-if="alertDone" style="margin:0; font:var(--type-small); color:var(--text-strong)">
                {{ alertDone }}
              </p>
              <template v-else>
                <Field
                  v-if="!session.authenticated"
                  label="Email me when tickets open"
                  help="One email about this event, with a one-click way out."
                >
                  <Input
                    v-model="alertEmail"
                    type="email"
                    placeholder="you@ic.ac.uk"
                    autocomplete="email"
                    @keyup.enter="canAlert && notifyMe()"
                  />
                </Field>
                <p v-else style="margin:0 0 var(--space-3); font:var(--type-2xs); color:var(--text-faint)">
                  We'll email {{ session.user?.email }} the moment tickets open.
                </p>
                <Button
                  block
                  variant="secondary"
                  :disabled="alertBusy || !canAlert"
                  @click="notifyMe"
                >
                  {{ alertBusy ? 'Saving…' : 'Email me when tickets go on sale' }}
                </Button>
              </template>
            </div>
          </div>

          <template v-else>
          <p v-if="!session.authenticated" style="margin:0 0 var(--space-4); font:var(--type-small); color:var(--text-muted)">
            Log in to book. Members and guests both welcome.
          </p>

          <div v-if="event.ticketTypes?.length" style="display:grid; gap:var(--space-4)">
            <Field label="Ticket type">
              <select
                v-model="chosen" class="ees-select"
              >
                <option
                  v-for="t in event.ticketTypes"
                  :key="t.id"
                  :value="t.id"
                  :disabled="!t.selectable"
                >
                  {{ t.name }} — {{ t.price }}<template v-if="t.soldOut"> (sold out)</template>
                  <template v-else-if="t.membersOnly"> · members only</template>
                  <template v-else-if="t.remaining !== null"> · {{ t.remaining }} left</template>
                </option>
              </select>
            </Field>

            <p v-if="selected?.description" style="margin:0; font:var(--type-small); color:var(--text-muted)">
              {{ selected.description }}
            </p>

            <Field v-if="maxQuantity > 1" label="How many">
              <Input v-model.number="quantity" type="number" min="1" :max="maxQuantity" />
            </Field>

            <!-- Guest details, revealed by the first press of Reserve. -->
            <template v-if="showGuestForm && !session.authenticated">
              <TraceRule />
              <div style="display:flex; align-items:center; justify-content:space-between; gap:var(--space-3)">
                <span style="font:var(--type-small); color:var(--text-muted)">
                  Your place is held while you fill this in.
                </span>
                <span
                  style="font-family:var(--font-mono); font-size:var(--text-sm); font-variant-numeric:tabular-nums"
                  :style="{ color: secondsLeft < 120 ? 'var(--danger)' : 'var(--text-accent)' }"
                >{{ countdown }}</span>
              </div>
              <Field label="Email" help="Where we send your booking and sign-in code.">
                <Input v-model="guest.email" type="email" placeholder="you@example.com" autocomplete="email" />
              </Field>
              <Field
                v-if="guestIsImperial"
                label="Imperial shortcode"
                help="The bit before @ic.ac.uk, e.g. ab1234. Lets us match your Union purchase automatically."
              >
                <Input v-model="guest.shortcode" placeholder="ab1234" autocomplete="username" />
              </Field>
              <div style="display:grid; grid-template-columns:1fr 1fr; gap:var(--space-3)">
                <Field label="First name"><Input v-model="guest.first_name" autocomplete="given-name" /></Field>
                <Field label="Surname"><Input v-model="guest.last_name" autocomplete="family-name" /></Field>
              </div>
              <div style="display:flex; gap:var(--space-4)">
                <button
                  type="button"
                  style="background:none; border:0; padding:0; cursor:pointer; font:var(--type-2xs); color:var(--text-faint); text-decoration:underline"
                  @click="emit('need-login')"
                >
                  Already have an account? Log in
                </button>
                <button
                  type="button"
                  style="background:none; border:0; padding:0; cursor:pointer; font:var(--type-2xs); color:var(--text-faint); text-decoration:underline"
                  @click="abandonForm"
                >
                  Cancel and release the ticket
                </button>
              </div>
            </template>

            <Button block :disabled="busy || !canBook" @click="book">
              {{ busy ? 'Holding…' : (showGuestForm && !session.authenticated ? 'Confirm and hold my ticket' : 'Reserve') }}
            </Button>

            <p
              v-if="needsMembership && !membershipWarning"
              style="margin:0; padding:var(--space-3); font:var(--type-2xs); color:var(--text-body); background:var(--surface-sunken); border-left:2px solid var(--amber-500, var(--text-accent))"
            >
              <strong>Members only.</strong> You can still book — we'll check your
              EESoc membership before confirming. If you've just joined, that's fine;
              the Union feed updates each morning.
            </p>
            <p
              v-if="membershipWarning"
              style="margin:0; padding:var(--space-3); font:var(--type-2xs); color:var(--text-body); background:var(--surface-sunken); border-left:2px solid var(--danger)"
            >{{ membershipWarning }}</p>
            <p v-if="message" style="margin:0; font:var(--type-small); color:var(--text-muted)">{{ message }}</p>
            <p v-if="error" style="margin:0; font:var(--type-small); color:var(--danger)">{{ error }}</p>
          </div>

          <p v-else style="margin:0; font:var(--type-small); color:var(--text-muted)">
            No tickets on sale for this one — just turn up.
          </p>

          </template>

          <template v-if="event.myReservations?.length">
            <TraceRule style="margin:var(--space-6) 0" />
            <h3 style="margin:0 0 var(--space-4); font:var(--type-h5)">Your bookings</h3>

            <div
              v-for="r in event.myReservations"
              :key="r.id"
              style="display:grid; gap:var(--space-3); padding:var(--space-4) 0; border-top:1px solid var(--border-faint)"
            >
              <div style="display:flex; justify-content:space-between; gap:var(--space-3); align-items:center">
                <span style="font:var(--type-small)">{{ r.ticketTypeName }} × {{ r.quantity }}</span>
                <Badge :tone="statusTone[r.status] || 'neutral'">{{ r.status }}</Badge>
              </div>

              <p v-if="r.status === 'held'" style="margin:0; font:var(--type-2xs); color:var(--text-faint)">
                Held until {{ new Date(r.expiresAt).toLocaleString() }}
              </p>

              <!-- The one thing left to do with an unpaid booking. -->
              <div v-if="r.status === 'held' && r.requiresPurchase && r.purchaseUrl">
                <Button size="sm" :href="r.purchaseUrl" target="_blank" rel="noopener">
                  Buy your ticket on the Union shop
                </Button>
              </div>

              <!-- Guests have no Union identity in the sales feed, so this is
                   the only way we can tie their purchase to this booking. -->
              <!-- A guest sees this booking because this browser made it, which
                   is not proof of who they are. Changing it needs the code. -->
              <p v-if="!session.authenticated" style="margin:0; font:var(--type-2xs); color:var(--text-muted)">
                Booked from this browser. To cancel it or add an order number,
                <a href="#" @click.prevent="emit('need-login')">sign in</a>
                with the code we emailed you.
              </p>

              <template v-if="session.authenticated && r.status === 'held' && r.requiresPurchase && !session.isMember && !r.orderNumber">
                <Field label="Union order number" help="From your receipt, e.g. AB99ZZ4.">
                  <Input v-model="orderNumbers[r.id]" placeholder="Order number" />
                </Field>
                <Button size="sm" :disabled="busy" @click="submitOrder(r)">Submit order number</Button>
              </template>

              <Button v-if="session.authenticated" size="sm" variant="ghost" :disabled="busy" @click="cancel(r)">Cancel</Button>
            </div>
          </template>
        </Card>
      </div>
    </template>
  </main>
</template>


<style scoped>
.event-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.6fr) minmax(300px, 1fr);
  gap: var(--space-8);
  align-items: start;
  margin-top: var(--space-6);
}

.event-main {
  display: grid;
  gap: var(--space-6);
  /* Without this a wide poster refuses to shrink: grid items default to
     min-content, so the image sets the floor and the booking column gets
     squeezed off the side. */
  min-width: 0;
}

/* The poster is artwork, not a banner. It used to be cropped to 21:9, which
   cut the top off anything portrait — on the Barnight poster that removed
   half the word. Letterbox instead, and cap the height so a tall image cannot
   push the booking panel below the fold. */
.event-media {
  display: flex;
  justify-content: center;
  background: var(--surface-sunken);
  border-radius: var(--radius-lg);
  overflow: hidden;
}

.event-image {
  display: block;
  max-width: 100%;
  max-height: min(520px, 70vh);
  width: auto;
  height: auto;
  object-fit: contain;
}

/* ImageSlot fills its parent absolutely, so the empty state needs a box with
   a size of its own. */
.event-media--empty {
  position: relative;
  aspect-ratio: 21 / 9;
}

.sales-notice {
  padding: var(--space-5);
  border: 1px solid var(--border-faint);
  border-radius: var(--radius-md);
  background: var(--surface-sunken);
}

/* Calendar & links: a quiet row of small links under the description. */
.calendar-links {
  display: grid;
  gap: var(--space-3);
}
.calendar-links__row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2) var(--space-5);
}
.calendar-links__row a {
  font: var(--type-small);
  color: var(--text-muted);
}
.calendar-links__row a:hover {
  color: var(--text-strong);
}

/* Separated from the "on sale soon" copy above it by a rule rather than space
   alone: it is an action, and it should not read as more of the same
   paragraph. */
.sales-alert {
  margin-top: var(--space-5);
  padding-top: var(--space-5);
  border-top: 1px solid var(--border-faint);
}

@media (max-width: 860px) {
  .event-layout {
    grid-template-columns: 1fr;
  }
}
</style>
