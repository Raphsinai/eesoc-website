<script setup>
// One event's bookings: who is holding, who has bought, who dropped out, who is
// waiting — with the actions that go with each, and a way to email any group.
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import api from '@/api'
import Badge from '@/components/ds/Badge.vue'
import Button from '@/components/ds/Button.vue'
import Card from '@/components/ds/Card.vue'
import Input from '@/components/ds/Input.vue'
import TraceRule from '@/components/ds/TraceRule.vue'
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

async function act(row, action) {
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    const body = action === 'confirm' ? { order_number: row.orderNumber || null } : undefined
    const res = await api.post(`/admin/reservations/${row.id}/${action}`, body)
    notice.value =
      res.message ||
      { remind: 'Reminder sent.', confirm: 'Marked as bought.', cancel: 'Cancelled.' }[action]
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
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

/** Opens the event's email dialog in the dashboard with this group ticked. */
function emailGroup(kind) {
  router.push({
    path: '/admin',
    query: { tab: 'events', announce: route.params.id, audience: `${kind}:${route.params.id}` },
  })
}

function csv() {
  const head = ['Name', 'Email', 'Shortcode', 'Member', 'Ticket', 'Qty', 'Status', 'Order number', 'Guest', 'Booked at', 'Hold until']
  const esc = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`
  const lines = rows.value.map((r) =>
    [r.name, r.email, r.shortcode, r.member ? 'yes' : '', r.ticketType, r.quantity, r.status,
      r.orderNumber, r.guest ? 'yes' : '', r.bookedAt, r.holdUntil].map(esc).join(','),
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

      <div v-if="can('site')" class="mail-actions">
        <Button :disabled="!counts.unpaid" @click="emailGroup('unpaid')">
          Email everyone who hasn't bought ({{ counts.unpaid }})
        </Button>
        <Button variant="secondary" :disabled="!counts.bought" @click="emailGroup('confirmed')">Email everyone who bought</Button>
        <Button variant="secondary" :disabled="!counts.booked" @click="emailGroup('booked')">Email everyone booked</Button>
        <Button variant="ghost" :disabled="!counts.waitlist" @click="emailGroup('waitlist')">Email the waitlist</Button>
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
        <Button size="sm" variant="ghost" :disabled="!rows.length" @click="csv">Download CSV</Button>
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
                  <template v-if="r.membersOnly && !r.member"> · <span style="color:var(--danger)">no membership seen</span></template>
                </div>
              </td>
              <td>{{ r.ticketType }} × {{ r.quantity }}</td>
              <td>
                <Badge v-if="!r.waitlist" :tone="tone[r.status] || 'neutral'">{{ r.status === 'held' ? 'not bought' : r.status }}</Badge>
                <span v-else class="muted">{{ r.status }}</span>
              </td>
              <td class="mono">{{ r.orderNumber || '—' }}</td>
              <td class="muted">{{ when(r.bookedAt) }}</td>
              <td class="muted">{{ r.status === 'held' ? when(r.holdUntil) : '—' }}</td>
              <td class="actions">
                <template v-if="r.status === 'held'">
                  <Button size="sm" variant="secondary" :disabled="busy" @click="act(r, 'remind')">Remind</Button>
                  <Button size="sm" variant="secondary" :disabled="busy" @click="act(r, 'confirm')">Mark bought</Button>
                </template>
                <select
                  v-if="(r.status === 'held' || r.status === 'confirmed') && data.summary.length > 1"
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
                  v-if="r.status === 'held' || r.status === 'confirmed'"
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
.ticket-switch {
  font: var(--type-2xs);
  padding: 4px 6px;
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  background: var(--surface-card);
  color: var(--text-strong);
}
</style>
