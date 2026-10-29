<script setup>
// One event's bookings: who is holding, who has bought, who dropped out, who is
// waiting — with the actions that go with each, and a way to email any group.
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import api from '@/api'
import Badge from '@/components/ds/Badge.vue'
import Button from '@/components/ds/Button.vue'
import Card from '@/components/ds/Card.vue'
import Field from '@/components/ds/Field.vue'
import Input from '@/components/ds/Input.vue'
import TraceRule from '@/components/ds/TraceRule.vue'
import ProductSalesImportPanel from '@/components/ProductSalesImportPanel.vue'
import { can } from '@/stores/session'

const route = useRoute()
const router = useRouter()

const data = ref(null)
const loading = ref(true)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const filter = ref('unpaid')
const query = ref('')

const editingOrder = ref(null)
const editedOrder = ref('')
const confirmingOrder = ref(null)
const confirmationOrder = ref('')
const confirmOverride = ref(false)

const FILTERS = [
  { id: 'unpaid', label: "Hasn't bought" },
  { id: 'bought', label: 'Bought' },
  { id: 'booked', label: 'All booked' },
  { id: 'closed', label: 'Cancelled / expired' },
  { id: 'waitlist', label: 'Waitlist' },
  { id: 'all', label: 'Everyone' },
]

async function load() {
  loading.value = true
  error.value = ''
  try {
    data.value = await api.get(`/admin/events/${route.params.id}/detail`)
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

onMounted(load)

const counts = computed(() => {
  const r = data.value?.reservations ?? []
  return {
    unpaid: r.filter((x) => x.status === 'held').length,
    bought: r.filter((x) => x.status === 'confirmed').length,
    booked: r.filter((x) => x.status === 'held' || x.status === 'confirmed').length,
    closed: r.filter((x) => x.status === 'cancelled' || x.status === 'expired').length,
    waitlist: data.value?.waitlist?.length ?? 0,
    all: r.length,
  }
})

const rows = computed(() => {
  if (!data.value) return []
  const term = query.value.trim().toLowerCase()
  let list
  if (filter.value === 'waitlist') {
    list = data.value.waitlist.map((w) => ({
      id: `w${w.id}`,
      name: null,
      email: w.email,
      ticketType:
        data.value.summary.find((s) => s.ticketTypeId === w.ticketTypeId)?.name ?? 'Any',
      quantity: w.quantity,
      status: `#${w.position} waiting`,
      bookedAt: w.joinedAt,
      waitlist: true,
    }))
  } else {
    const keep = {
      unpaid: (x) => x.status === 'held',
      bought: (x) => x.status === 'confirmed',
      booked: (x) => x.status === 'held' || x.status === 'confirmed',
      closed: (x) => x.status === 'cancelled' || x.status === 'expired',
      all: () => true,
    }[filter.value]
    list = data.value.reservations.filter(keep)
  }
  if (!term) return list
  return list.filter((x) =>
    [x.name, x.email, x.shortcode, x.orderNumber, x.ticketType]
      .filter(Boolean)
      .some((v) => String(v).toLowerCase().includes(term)),
  )
})

const tone = { held: 'accent', confirmed: 'success', cancelled: 'neutral', expired: 'neutral' }
const when = (iso) =>
  iso
    ? new Date(iso).toLocaleString(undefined, {
        day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
      })
    : '—'

function startConfirmation(row) {
  confirmingOrder.value = row.id
  confirmationOrder.value = row.orderNumber || ''
  confirmOverride.value = false
}

async function submitConfirmation(row) {
  if (row.requiresPurchase !== false && !confirmOverride.value && !confirmationOrder.value.trim()) {
    error.value = 'Enter a Union order number to verify, or use the separately permitted override.'
    return
  }
  if (confirmOverride.value && !confirm('Confirm this ticket without verifying a Union order? This will issue the ticket immediately.')) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    const response = await api.post(`/admin/reservations/${row.id}/confirm`, {
      order_number: confirmationOrder.value.trim() || null,
      override: confirmOverride.value,
    })
    notice.value = response.message || 'Confirmation updated.'
    confirmingOrder.value = null
    await load()
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}

async function act(row, action) {
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    const res = await api.post(`/admin/reservations/${row.id}/${action}`)
    notice.value =
      res.message ||
      { remind: 'Reminder sent.', cancel: 'Cancelled.' }[action]
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

function startOrderEdit(row) {
  editingOrder.value = row.id
  editedOrder.value = row.orderNumber || ''
}
async function saveOrder(row) {
  busy.value = true
  error.value = ''
  try {
    const result = await api.patch(`/admin/reservations/${row.id}/order-number`, { order_number: editedOrder.value.trim() || null })
    editingOrder.value = null
    notice.value = result.message || 'Order number saved.'
    await load()
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}

/** Move a booking to another ticket type of this event. */
async function changeTicket(row, event) {
  const ticketTypeId = Number(event.target.value)
  event.target.value = ''
  if (!ticketTypeId) return
  const to = data.value.summary.find((s) => s.ticketTypeId === ticketTypeId)?.name
  if (!confirm(`Change ${row.name || row.email}'s ticket from ${row.ticketType} to ${to}?`)) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    const res = await api.post(`/admin/reservations/${row.id}/ticket-type`, { ticket_type_id: ticketTypeId })
    notice.value = res.message
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

// ------------------------------------------------------------ add a ticket
// Paid tickets begin as holds until a real Union sale is found, unless the
// person adding them has the separate payment-verification override grant.
const adding = ref(false)
const blankTicket = () => ({
  ticket_type_id: '', email: '', first_name: '', last_name: '', quantity: 1, note: '',
  order_number: '', override_confirmation: false, confirm_immediately: false,
  send_confirmation: true, override_capacity: false,
})
const ticket = reactive(blankTicket())
const selectedAddType = computed(() => data.value?.summary?.find(
  t => Number(t.ticketTypeId) === Number(ticket.ticket_type_id),
))
const paidAddType = computed(() => selectedAddType.value?.requiresPurchase !== false)

function openAdd() {
  Object.assign(ticket, blankTicket(), { ticket_type_id: data.value?.summary?.[0]?.ticketTypeId ?? '' })
  adding.value = true
}

async function addTicket() {
  if (!ticket.ticket_type_id || !ticket.email.trim()) {
    error.value = 'Choose a ticket type and give their email address.'
    return
  }
  if (paidAddType.value && !ticket.order_number.trim() && !ticket.override_confirmation) {
    error.value = 'A Union order number is required unless you have payment-verification override permission.'
    return
  }
  if (ticket.override_confirmation) ticket.confirm_immediately = true
  if (ticket.override_confirmation && !confirm(
    'Confirm this paid ticket without evidence from a Union sale? This is a separate permissioned override.')) return
  if (ticket.override_capacity && !confirm(
    'Bypass the event and ticket-type capacity limits? This can overbook the event.')) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    const res = await api.post(`/admin/events/${route.params.id}/tickets`, {
      ...ticket,
      order_number: ticket.order_number.trim() || null,
      ticket_type_id: Number(ticket.ticket_type_id),
      quantity: Math.max(1, Number(ticket.quantity) || 1),
    })
    notice.value = res.message
    adding.value = false
    filter.value = res.reservation?.status === 'held' ? 'unpaid' : 'bought'
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

// ------------------------------------------------------ confirmation email
// What happened to each confirmed booking's "you're confirmed" email.
function emailState(r) {
  if (r.status !== 'confirmed') return null
  const at = r.confirmationSentAt ? ` ${when(r.confirmationSentAt)}` : ''
  return {
    sent: { text: `email sent${at}`, tone: 'muted' },
    sending: { text: 'email sending…', tone: 'muted' },
    failed: { text: `email failed (${r.confirmationAttempts} ${r.confirmationAttempts === 1 ? 'try' : 'tries'})`, tone: 'danger' },
    skipped: { text: 'no confirmation email', tone: 'muted' },
    suppressed: { text: 'email not delivered here (test site)', tone: 'muted' },
  }[r.confirmationEmail] ?? { text: 'email pending', tone: 'muted' }
}

async function resend(row) {
  const who = row.name || row.email
  const again = row.confirmationEmail === 'sent'
  if (!confirm(
    again
      ? `Send ${who} their confirmation email again? They already got one${row.confirmationSentAt ? ` on ${when(row.confirmationSentAt)}` : ''}.`
      : `Send ${who} a confirmation email now?`,
  )) return
  busy.value = true
  error.value = ''
  try {
    const res = await api.post(`/admin/reservations/${row.id}/confirmation`)
    notice.value = res.message
    // The send happens in the background; give it a moment before re-reading.
    setTimeout(load, 2500)
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

/** Opens the event's email dialog in the dashboard with this group ticked. */
function emailGroup(kind) {
  router.push({
    path: '/admin',
    query: { tab: 'events', announce: route.params.id, audience: `${kind}:${route.params.id}` },
  })
}

function csv() {
  const head = ['Name', 'Email', 'Shortcode', 'Member', 'Ticket', 'Qty', 'Status', 'Order number', 'Guest', 'Booked at', 'Hold until', 'Added by committee', 'Confirmation email', 'Confirmation sent at']
  const esc = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`
  const lines = rows.value.map((r) =>
    [r.name, r.email, r.shortcode, r.member ? 'yes' : '', r.ticketType, r.quantity, r.status,
      r.orderNumber, r.guest ? 'yes' : '', r.bookedAt, r.holdUntil,
      r.source === 'committee' ? 'yes' : '', r.status === 'confirmed' ? (r.confirmationEmail || 'pending') : '',
      r.confirmationSentAt || ''].map(esc).join(','),
  )
  const blob = new Blob([[head.join(','), ...lines].join('\n')], { type: 'text/csv' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `${data.value.event.slug || 'event'}-${filter.value}.csv`
  a.click()
  URL.revokeObjectURL(a.href)
}
</script>

<template>
  <main style="max-width:var(--container-xl, 1200px); margin:0 auto; padding:var(--space-10) var(--space-6); min-height:70vh">
    <RouterLink to="/admin?tab=events" style="font:var(--type-small)">← All events</RouterLink>

    <p v-if="loading" style="font:var(--type-small); color:var(--text-faint)">Loading…</p>
    <p v-if="error" style="font:var(--type-small); color:var(--danger)">{{ error }}</p>

    <template v-if="data">
      <header style="display:grid; gap:var(--space-2); margin-top:var(--space-4)">
        <h1 style="margin:0; font:var(--type-h2)">{{ data.event.title }}</h1>
        <span style="font:var(--type-small); color:var(--text-muted)">
          {{ data.event.date }} · {{ data.event.time }}<template v-if="data.event.venue"> · {{ data.event.venue }}</template>
          · capacity {{ data.event.capacity ?? '∞' }}
          <Badge :tone="data.event.published ? 'success' : 'neutral'">{{ data.event.published ? 'Live' : 'Draft' }}</Badge>
        </span>
        <div><Button v-if="can('events.checkin.view')" size="sm" variant="secondary" @click="router.push(`/checkin/${data.event.id}`)">Open door check-in</Button></div>
      </header>

      <div class="summary">
        <Card v-for="s in data.summary" :key="s.ticketTypeId" variant="outline">
          <div style="display:grid; gap:var(--space-2)">
            <strong style="font:var(--type-h5)">{{ s.name }} <span style="color:var(--text-muted); font-weight:400">{{ s.price }}</span></strong>
            <div class="summary__nums">
              <span><b>{{ s.unpaid }}</b> not bought</span>
              <span><b>{{ s.bought }}</b> bought</span>
              <span><b>{{ s.closed }}</b> dropped</span>
              <span><b>{{ s.waitlist }}</b> waiting</span>
            </div>
            <span style="font:var(--type-2xs); color:var(--text-faint)">
              {{ s.unpaid + s.bought }} of {{ s.capacity ?? '∞' }} places taken
            </span>
          </div>
        </Card>
      </div>

      <div v-if="can('email')" class="mail-actions">
        <Button v-if="can('email.announce_event')" :disabled="!counts.unpaid" @click="emailGroup('unpaid')">
          Email everyone who hasn't bought ({{ counts.unpaid }})
        </Button>
        <Button v-if="can('email.announce_event')" variant="secondary" :disabled="!counts.bought" @click="emailGroup('confirmed')">Email everyone who bought</Button>
        <Button v-if="can('email.announce_event')" variant="secondary" :disabled="!counts.booked" @click="emailGroup('booked')">Email everyone booked</Button>
        <Button v-if="can('email.announce_event')" variant="ghost" :disabled="!counts.waitlist" @click="emailGroup('waitlist')">Email the waitlist</Button>
      </div>

      <ProductSalesImportPanel v-if="can('events.bookings.import.view')" :event-id="Number(route.params.id)" @refresh="load" />

      <div class="add-ticket">
        <Button v-if="(!adding) && (can('events.bookings.add'))" variant="secondary" :disabled="!data.summary.length" @click="openAdd">Add a ticket for someone</Button>
        <Card v-else variant="offset">
          <form style="display:grid; gap:var(--space-4)" @submit.prevent="addTicket">
            <strong style="font:var(--type-h5)">Add a ticket</strong>
            <p style="margin:0; font:var(--type-small); color:var(--text-muted)">
              Paid tickets require a Union order number and remain held until a matching sale is found.
              If not verified after the next successful daily sync, the booking enters Review cancellations.
              Free tickets and explicitly authorised payment overrides confirm immediately.
            </p>
            <div class="add-grid">
              <Field label="Ticket type">
                <select v-model="ticket.ticket_type_id" class="ees-select" required>
                  <option v-for="t in data.summary" :key="t.ticketTypeId" :value="t.ticketTypeId">
                    {{ t.name }} · {{ t.price }} · {{ t.unpaid + t.bought }}/{{ t.capacity ?? '∞' }} taken
                  </option>
                </select>
              </Field>
              <Field label="How many"><Input v-model.number="ticket.quantity" type="number" min="1" max="20" /></Field>
            </div>
            <div class="add-grid">
              <Field label="Email"><Input v-model="ticket.email" type="email" placeholder="ab1234@ic.ac.uk" required /></Field>
              <Field label="First name" help="Optional for members."><Input v-model="ticket.first_name" /></Field>
              <Field label="Last name"><Input v-model="ticket.last_name" /></Field>
            </div>
            <Field v-if="paidAddType" label="Union order number" help="Required for paid tickets unless you have payment-verification override permission. The Union sale must match before this ticket is confirmed.">
              <Input v-model="ticket.order_number" maxlength="128" placeholder="From the Union purchase receipt" :required="!ticket.override_confirmation" />
            </Field>
            <label v-if="can('events.bookings.confirm') || can('events.bookings.confirm_override')"
              style="display:flex; gap:var(--space-2); align-items:center; font:var(--type-small)">
              <input v-model="ticket.confirm_immediately" type="checkbox" :disabled="ticket.override_confirmation" />
              {{ paidAddType ? 'Try to confirm immediately if the Union order already matches a sale' : 'Confirm this free ticket immediately' }}
            </label>
            <p v-if="!can('events.bookings.confirm') && !can('events.bookings.confirm_override')"
              style="font:var(--type-small); color:var(--text-muted)">
              You can add an unconfirmed booking. Only authorised staff can confirm it immediately.
            </p>
            <label v-if="paidAddType && can('events.bookings.confirm_override')" style="display:flex; gap:var(--space-2); align-items:center; font:var(--type-small)">
              <input v-model="ticket.override_confirmation" type="checkbox" />
              Override payment verification — confirm immediately without a Union sale
            </label>
            <p v-if="ticket.override_confirmation && paidAddType" style="margin:0;color:var(--danger);font:var(--type-small)">
              This grants a ticket without verifying payment. It is audited separately from a capacity override.
            </p>
            <Field label="Note" help="Optional explanation for the committee.">
              <Input v-model="ticket.note" />
            </Field>
            <label style="display:flex; gap:var(--space-2); align-items:center; font:var(--type-small)">
              <input v-model="ticket.send_confirmation" type="checkbox" />
              {{ paidAddType && !ticket.override_confirmation ? 'Email confirmation once payment matches' : 'Email them a confirmation' }}
            </label>
            <label v-if="can('events.bookings.capacity_override')" style="display:flex; gap:var(--space-2); align-items:center; font:var(--type-small)">
              <input v-model="ticket.override_capacity" type="checkbox" />
              Override event and ticket-type capacity
            </label>
            <p v-if="ticket.override_capacity && can('events.bookings.capacity_override')" style="margin:0;color:var(--danger);font:var(--type-small)">
              This bypasses capacity restrictions. You will be asked to confirm before adding the ticket.
            </p>
            <div style="display:flex; gap:var(--space-3)">
              <Button v-if="can('events.bookings.add')" type="submit" :disabled="busy || (paidAddType && !ticket.order_number.trim() && !ticket.override_confirmation)">{{ busy ? 'Adding…' : paidAddType && !ticket.override_confirmation ? 'Add booking for verification' : 'Add ticket' }}</Button>
              <Button variant="ghost" type="button" @click="adding = false">Cancel</Button>
            </div>
          </form>
        </Card>
      </div>

      <TraceRule style="margin:var(--space-6) 0" />

      <div class="filters">
        <button
          v-for="f in FILTERS"
          :key="f.id"
          type="button"
          class="chip"
          :class="{ 'is-active': filter === f.id }"
          @click="filter = f.id"
        >
          {{ f.label }} <span>{{ counts[f.id] }}</span>
        </button>
        <Input v-model="query" placeholder="Search name, email, shortcode, order…" style="max-width:320px" />
        <Button v-if="can('events.bookings.export')" size="sm" variant="ghost" :disabled="!rows.length" @click="csv">Download CSV</Button>
      </div>

      <p v-if="notice" style="font:var(--type-small); color:var(--text-strong)">{{ notice }}</p>

      <div class="table-wrap">
        <table class="people">
          <thead>
            <tr>
              <th>Person</th><th>Ticket</th><th>Status</th><th>Order</th><th>Booked</th><th>Hold until</th><th />
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in rows" :key="r.id">
              <td>
                <div>{{ r.name || r.email }}</div>
                <div class="muted">
                  <template v-if="r.name">{{ r.email }}</template>
                  <template v-if="r.shortcode"> · {{ r.shortcode }}</template>
                  <template v-if="r.guest"> · guest</template>
                  <template v-if="r.source === 'committee'"> · added by committee</template>
                  <template v-if="r.membersOnly && !r.member"> · <span style="color:var(--danger)">no membership seen</span></template>
                </div>
              </td>
              <td>{{ r.ticketType }} × {{ r.quantity }}</td>
              <td>
                <Badge v-if="!r.waitlist" :tone="tone[r.status] || 'neutral'">{{ r.status === 'held' ? 'not bought' : r.status }}</Badge>
                <span v-else class="muted">{{ r.status }}</span>
                <div v-if="r.orderVerification?.status === 'pending'" class="email-state"
                     :title="`Order will be checked after ${when(r.orderVerification.check_after)} UTC if still unmatched`">
                  Order awaiting next Union sales sync
                </div>
                <div v-if="r.orderVerification?.status === 'rejected'" class="email-state"
                     style="color:var(--danger)">
                  Union order rejected — review cancellation
                </div>
                <div
                  v-if="emailState(r)"
                  class="email-state"
                  :style="emailState(r).tone === 'danger' ? 'color:var(--danger)' : ''"
                  :title="r.note || ''"
                >{{ emailState(r).text }}</div>
              </td>
              <td class="mono">
                <template v-if="!r.waitlist && editingOrder === r.id">
                  <input v-model="editedOrder" maxlength="128" aria-label="Order number" class="order-edit" :disabled="busy" @keyup.enter="saveOrder(r)" @keyup.esc="editingOrder = null" />
                  <Button v-if="can('events.bookings.order_number.edit')" size="sm" :disabled="busy" @click="saveOrder(r)">Save</Button>
                  <Button v-if="can('events.bookings.order_number.edit')" size="sm" variant="ghost" @click="editingOrder = null">Cancel</Button>
                </template>
                <template v-else>
                  {{ r.orderNumber || '—' }}
                  <Button v-if="!r.waitlist && can('events.bookings.order_number.edit')" size="sm" variant="ghost" :disabled="busy" @click="startOrderEdit(r)">Edit</Button>
                </template>
              </td>
              <td class="muted">{{ when(r.bookedAt) }}</td>
              <td class="muted">{{ r.status === 'held' ? when(r.holdUntil) : '—' }}</td>
              <td class="actions">
                <div v-if="confirmingOrder === r.id" class="confirmation-edit">
                  <input v-if="r.requiresPurchase !== false" v-model="confirmationOrder" class="order-edit" maxlength="128"
                         aria-label="Union order number to verify" placeholder="Union order number"
                         :disabled="busy" @keyup.enter="submitConfirmation(r)" />
                  <label v-if="r.requiresPurchase !== false && can('events.bookings.confirm_override')" class="confirm-override">
                    <input v-model="confirmOverride" type="checkbox" />
                    Override: confirm without verifying payment
                  </label>
                  <span v-if="confirmOverride" style="color:var(--danger);font:var(--type-small)">
                    This immediately confirms the ticket without a Union sale match.
                  </span>
                  <Button v-if="can('events.bookings.confirm') || can('events.bookings.confirm_override')"
                          size="sm" :disabled="busy || (r.requiresPurchase !== false && !confirmOverride && !confirmationOrder.trim())"
                          @click="submitConfirmation(r)">
                    {{ r.requiresPurchase === false ? 'Confirm free ticket' : confirmOverride ? 'Confirm override' : 'Verify order' }}
                  </Button>
                  <Button v-if="can('events.bookings.confirm') || can('events.bookings.confirm_override')"
                          size="sm" variant="ghost" @click="confirmingOrder = null">Cancel</Button>
                </div>
                <template v-if="r.status === 'held'">
                  <Button v-if="can('events.bookings.remind')" size="sm" variant="secondary" :disabled="busy" @click="act(r, 'remind')">Remind</Button>
                  <Button v-if="can('events.bookings.confirm') || can('events.bookings.confirm_override')"
                          size="sm" variant="secondary" :disabled="busy"
                          @click="startConfirmation(r)">{{ r.requiresPurchase === false ? 'Confirm free ticket…' : 'Confirm purchase…' }}</Button>
                </template>
                <Button
                  v-if="(r.status === 'confirmed' && ['sent', 'failed', 'skipped', 'suppressed'].includes(r.confirmationEmail)) && (can('events.bookings.resend'))"
                  size="sm" variant="ghost" :disabled="busy"
                  @click="resend(r)"
                >{{ r.confirmationEmail === 'sent' ? 'Resend email' : 'Send email' }}</Button>
                <select
                  v-if="(r.status === 'held' || r.status === 'confirmed') && data.summary.length > 1 && can('events.bookings.change_ticket')"
                  class="ticket-switch"
                  :disabled="busy"
                  :aria-label="`Change ${r.name || r.email}'s ticket type`"
                  @change="changeTicket(r, $event)"
                >
                  <option value="">Change ticket…</option>
                  <option
                    v-for="t in data.summary.filter((s) => s.ticketTypeId !== r.ticketTypeId)"
                    :key="t.ticketTypeId"
                    :value="t.ticketTypeId"
                  >{{ t.name }}</option>
                </select>
                <Button
                  v-if="(r.status === 'held' || r.status === 'confirmed') && (can('events.bookings.cancel'))"
                  size="sm" variant="ghost" :disabled="busy"
                  @click="act(r, 'cancel')"
                >Cancel</Button>
              </td>
            </tr>
            <tr v-if="!rows.length">
              <td colspan="7" class="muted" style="padding:var(--space-6)">Nobody here.</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </main>
</template>

<style scoped>
.summary {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: var(--space-4);
  margin-top: var(--space-6);
}
.summary__nums {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2) var(--space-4);
  font: var(--type-small);
  color: var(--text-muted);
}
.summary__nums b { color: var(--text-strong); font-size: var(--text-lg); }
.mail-actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  margin-top: var(--space-6);
}
.filters {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  align-items: center;
  margin-bottom: var(--space-4);
}
.chip {
  font: var(--type-small);
  padding: 4px 10px;
  border: var(--border-width-hairline) solid var(--border-default);
  border-radius: 999px;
  background: var(--surface-card);
  color: var(--text-body);
  cursor: pointer;
}
.chip span { color: var(--text-faint); margin-left: 4px; }
.chip.is-active { background: var(--ink-900, #111); color: #fff; border-color: transparent; }
.chip.is-active span { color: rgba(255,255,255,.7); }
.table-wrap { overflow-x: auto; }
.people { width: 100%; border-collapse: collapse; font: var(--type-small); }
.people th {
  text-align: left; font: var(--type-label); color: var(--text-muted);
  padding: var(--space-2) var(--space-3); border-bottom: var(--border-width-hairline) solid var(--border-default);
}
.people td {
  padding: var(--space-3); border-bottom: var(--border-width-hairline) solid var(--border-hairline);
  vertical-align: top;
}
.muted { color: var(--text-muted); font-size: var(--text-xs); }
.mono { font-family: var(--font-mono); font-size: var(--text-xs); }
.actions { display: flex; gap: var(--space-2); flex-wrap: wrap; justify-content: flex-end; }
.confirmation-edit { display:grid; gap:var(--space-2); min-width:190px; }
.confirm-override { display:flex; align-items:center; gap:var(--space-2); font:var(--type-small); }
.order-edit { max-width: 125px; padding: 4px; border: 1px solid var(--border-default); }
.ticket-switch {
  font: var(--type-2xs);
  padding: 4px 6px;
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  background: var(--surface-card);
  color: var(--text-strong);
}
.add-ticket {
  margin-top: var(--space-6);
  max-width: 820px;
}
.add-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: var(--space-4);
}
.email-state {
  margin-top: 4px;
  font: var(--type-2xs);
  color: var(--text-faint);
  white-space: nowrap;
}
</style>
