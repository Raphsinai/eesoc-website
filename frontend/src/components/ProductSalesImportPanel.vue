<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import api from '@/api'
import { can } from '@/stores/session'
import Card from '@/components/ds/Card.vue'
import Button from '@/components/ds/Button.vue'
import SaleMatchExplorer from '@/components/SaleMatchExplorer.vue'

const props = defineProps({
  eventId: { type: Number, required: true },
  societyId: { type: Number, default: null },
  permissions: { type: Array, default: () => [] },
})
const allowed = key => props.societyId ? props.permissions.includes(key) : can(key)
const salesBase = computed(() => props.societyId
  ? `/society-portal/${props.societyId}/events/${props.eventId}/product-sales`
  : '/admin/product-sales')
const emit = defineEmits(['refresh'])
const targets = ref([])
const imports = ref([])
const rows = ref([])
const file = ref(null)
const targetId = ref('')
const busy = ref(false)
const error = ref('')
const notice = ref('')
const detailForm = reactive({})
const activeMatchId = ref(null)
const activeMatch = computed(() => rows.value.find(row => row.id === activeMatchId.value) || null)
function openMatch(row) { activeMatchId.value = activeMatchId.value === row.id ? null : row.id }
async function linkedReservation(row) {
  notice.value = `Linked reservation #${row.id} and confirmed the ticket against its Union sale.`
  activeMatchId.value = null
  await refresh()
  emit('refresh')
}
const shownReview = ref(30)
const shownUnreserved = ref(30)

const review = computed(() => rows.value.filter(row => row.status === 'review'))
const unreserved = computed(() => rows.value.filter(row => row.status === 'unreserved'))
const matched = computed(() => rows.value.filter(row => row.status === 'confirmed'))
const duplicates = computed(() => rows.value.filter(row => row.status === 'duplicate'))
const completed = computed(() => rows.value.filter(row => row.status === 'ticket_added'))
const targetOptions = computed(() => targets.value.filter(t => t.event_id === props.eventId))

function onFile(event) {
  file.value = event.target.files?.[0] || null
  const digits = file.value?.name?.match(/\d{3,11}/g) || []
  const options = targetOptions.value.filter(t => digits.some(d =>
    Number(d) === Number(t.product_id) ||
    (Number(t.product_id) > 1000000 && Number(d) === Number(t.product_id) - 1000000),
  ))
  if (options.length && new Set(options.map(t => t.ticket_type_id)).size === 1) {
    targetId.value = String(options[0].ticket_type_id)
  }
}

function names(row) {
  const words = (row.buyer_name || '').trim().split(/\s+/)
  return { first_name: words.slice(0, -1).join(' '), last_name: words.length > 1 ? words.at(-1) : '' }
}

async function refresh() {
  if (!allowed('events.bookings.import.view')) return
  error.value = ''
  try {
    const [items, batches] = await Promise.all([
      api.get(`${salesBase.value}/rows`, { event_id: props.eventId }),
      api.get(`${salesBase.value}/imports`, { event_id: props.eventId }),
    ])
    rows.value = items.rows || []
    imports.value = batches.imports || []
    for (const row of rows.value) {
      if (row.status === 'unreserved' && !detailForm[row.id]) {
        detailForm[row.id] = {
          email: row.email || '', ...names(row), note: '', send_confirmation: true,
          override_capacity: false,
        }
      }
    }
  } catch (e) { error.value = e.message }
}

async function initialize() {
  try {
    targets.value = (await api.get(`${salesBase.value}/targets`)).targets || []
    await refresh()
  } catch (e) { error.value = e.message }
}
onMounted(initialize)

async function upload() {
  if (!file.value || !targetId.value) {
    error.value = 'Choose a sales file and the ticket type it belongs to.'
    return
  }
  busy.value = true
  error.value = ''
  try {
    const form = new FormData()
    form.append('file', file.value)
    form.append('ticket_type_id', targetId.value)
    const result = await api.post(`${salesBase.value}/imports`, form)
    notice.value = result.existing
      ? 'This file has already been imported. No duplicate tickets were issued.'
      : `Imported ${Object.values(result.counts || {}).reduce((sum, value) => sum + value, 0)} sales: ${result.counts.confirmed || 0} confirmed, ${result.counts.review || 0} need approval, ${result.counts.unreserved || 0} have no reservation.`
    await refresh()
    emit('refresh')
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}

async function addTicket(row) {
  const details = detailForm[row.id]
  if (!details?.email?.trim()) { error.value = 'An email address is required to issue a ticket.'; return }
  if (details.override_capacity && !confirm('Override event capacity for this ticket?')) return
  if (!confirm(`Issue ${row.quantity} ticket(s) to ${details.email}? This will confirm the ticket immediately.`)) return
  busy.value = true
  error.value = ''
  try {
    await api.post(`${salesBase.value}/rows/${row.id}/create-ticket`, details)
    notice.value = `Ticket issued to ${details.email}.`
    await refresh()
    emit('refresh')
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}
</script>

<template>
  <section class="sales-import" aria-label="Product sales reconciliation">
    <Card variant="offset">
      <h2 style="font:var(--type-h4);margin:0">Union product sales import</h2>
      <p style="font:var(--type-small); color:var(--text-muted);margin:0">
        Upload a CSV or Excel sales export with the product ID in its filename. CID matches take priority;
        only one-to-one exact matches are confirmed automatically. Similar names require approval,
        and purchases with no reservation remain in a separate queue.
      </p>
      <div v-if="allowed('events.bookings.import') && allowed('events.bookings.confirm')" class="sales-upload">
        <label class="sales-field">Sales file (.csv, .xls or .xlsx)
          <input type="file" accept=".csv,.xls,.xlsx" :disabled="busy" @change="onFile" />
        </label>
        <label class="sales-field">Ticket type for this product
          <select v-model="targetId" class="ees-select" :disabled="busy">
            <option value="">Select ticket type</option>
            <option v-for="t in targetOptions" :key="`${t.product_id}-${t.ticket_type_id}-${t.product_line_id}`" :value="String(t.ticket_type_id)">
              {{ t.ticket_type_name }} · {{ t.product_id > 1000000 ? `SUMS #${t.product_id - 1000000} (API #${t.product_id})` : `product #${t.product_id}` }}{{ t.product_line_id ? ` · line #${t.product_line_id}` : '' }}
            </option>
          </select>
        </label>
        <Button v-if="allowed('events.bookings.import') && allowed('events.bookings.confirm')" :disabled="busy || !file || !targetId" @click="upload">{{ busy ? 'Importing…' : 'Import and reconcile' }}</Button>
      </div>
      <p v-if="error" style="color:var(--danger);font:var(--type-small);margin:0" role="alert">{{ error }}</p>
      <p v-if="notice" style="font:var(--type-small);margin:0" role="status">{{ notice }}</p>
      <div v-if="imports.length" style="font:var(--type-small);color:var(--text-muted)">
        Most recent: {{ imports[0].filename }} · {{ imports[0].total }} rows · {{ new Date(imports[0].uploaded_at).toLocaleString() }}
      </div>
      <div class="sales-stats">
        <span>{{ matched.length }} confirmed</span>
        <span>{{ review.length }} need approval</span>
        <span>{{ unreserved.length }} no reservation</span>
        <span>{{ completed.length }} tickets added</span>
        <span>{{ duplicates.length }} duplicates</span>
      </div>
    </Card>

    <SaleMatchExplorer v-if="activeMatch && allowed('events.bookings.import.view')" :sale="activeMatch" :society-id="societyId" :event-id="eventId" :permissions="permissions"
      @close="activeMatchId = null" @linked="linkedReservation" />

    <Card v-if="review.length && allowed('events.bookings.import.view')" variant="offset">
      <h3 style="font:var(--type-h5);margin:0">Matches needing approval ({{ review.length }})</h3>
      <p style="font:var(--type-small);color:var(--text-muted);margin:0">No ticket is confirmed until an authorised committee member selects and approves a matching reservation.</p>
      <div v-for="row in review.slice(0,shownReview)" :key="row.id" class="sales-row">
        <div><b>{{ row.buyer_name || 'Unnamed buyer' }}</b> · {{ row.cid || 'no CID' }} · {{ row.ticket_type_name }} · qty {{ row.quantity }}</div>
        <div class="sales-detail">Order {{ row.order_number || 'not supplied' }} · {{ row.matched_on }}</div>
        <div class="sales-actions">
          <Button size="sm" variant="secondary" @click="openMatch(row)">
            {{ activeMatchId === row.id ? 'Close search' : 'Search and link reservation' }}
          </Button>
          <span v-if="row.suggestions?.length" class="sales-detail">
            {{ row.suggestions.length }} suggested match(es) — open search to review
          </span>
        </div>
      </div>
      <Button v-if="review.length > shownReview" size="sm" variant="secondary" @click="shownReview += 30">Show more</Button>
    </Card>

    <Card v-if="unreserved.length && allowed('events.bookings.import.view')" variant="offset">
      <h3 style="font:var(--type-h5);margin:0">Bought but did not reserve ({{ unreserved.length }})</h3>
      <p style="font:var(--type-small);color:var(--text-muted);margin:0">Search for an existing reservation first, including by email or reservation ID. If none exists, complete the details to issue a ticket. No ticket is created automatically.</p>
      <div v-for="row in unreserved.slice(0,shownUnreserved)" :key="row.id" class="sales-row">
        <div><b>{{ row.buyer_name || 'Unnamed buyer' }}</b> · CID {{ row.cid || 'not given' }} · {{ row.ticket_type_name }} · qty {{ row.quantity }}</div>
        <div class="sales-detail">Order {{ row.order_number || 'not supplied' }} · {{ row.filename }}</div>
        <div class="sales-actions">
          <Button size="sm" variant="secondary" @click="openMatch(row)">
            {{ activeMatchId === row.id ? 'Close search' : 'Find existing reservation to link' }}
          </Button>
        </div>
        <div v-if="allowed('events.bookings.add') && allowed('events.bookings.confirm') && detailForm[row.id]" class="sales-form">
          <label>Email<input v-model="detailForm[row.id].email" type="email" required placeholder="name@example.com" /></label>
          <label>First name<input v-model="detailForm[row.id].first_name" type="text" /></label>
          <label>Last name<input v-model="detailForm[row.id].last_name" type="text" /></label>
          <label>Note<input v-model="detailForm[row.id].note" type="text" placeholder="Optional" /></label>
          <label class="sales-check"><input v-model="detailForm[row.id].send_confirmation" type="checkbox" /> Email confirmation</label>
          <label v-if="allowed('events.bookings.capacity_override')" class="sales-check"><input v-model="detailForm[row.id].override_capacity" type="checkbox" /> Override capacity</label>
          <Button v-if="allowed('events.bookings.add')" size="sm" :disabled="busy || !detailForm[row.id].email.trim()" @click="addTicket(row)">Issue ticket</Button>
        </div>
        <p v-else class="sales-detail">Issuing a confirmed ticket from an imported purchase requires Add tickets and Confirm tickets permissions. You can still search for an existing booking to link.</p>
      </div>
      <Button v-if="unreserved.length > shownUnreserved" size="sm" variant="secondary" @click="shownUnreserved += 30">Show more</Button>
    </Card>
  </section>
</template>

<style scoped>
.sales-import { display:grid; gap:var(--space-4); min-width:0; }
.sales-upload { display:flex; flex-wrap:wrap; align-items:end; gap:var(--space-3); margin-top:var(--space-4); }
.sales-field { display:grid; gap:var(--space-2); font:var(--type-small); min-width:190px; max-width:100%; }
.sales-stats { display:flex; flex-wrap:wrap; gap:var(--space-4); font:var(--type-small); padding-top:var(--space-3); }
.sales-row { border-top:1px solid var(--border-hairline); padding:var(--space-3) 0; display:grid; gap:var(--space-2); font:var(--type-small); }
.sales-detail { color:var(--text-muted); font:var(--type-small); }
.sales-actions { display:flex; flex-wrap:wrap; gap:var(--space-2); }
.sales-form { display:flex; flex-wrap:wrap; gap:var(--space-3); align-items:end; }
.sales-form label { display:grid; gap:var(--space-1); min-width:140px; }
.sales-form input:not([type=checkbox]) { width:100%; min-height:32px; }
.sales-form .sales-check { display:flex; align-items:center; gap:var(--space-1); min-width:auto; }
@media(max-width:620px) { .sales-upload,.sales-form,.sales-actions { display:grid; grid-template-columns:minmax(0,1fr); } }
</style>
