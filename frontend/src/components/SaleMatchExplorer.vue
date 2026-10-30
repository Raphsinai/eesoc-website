<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import api from '@/api'
import { can } from '@/stores/session'
import Button from '@/components/ds/Button.vue'

const props = defineProps({
  sale: { type: Object, required: true },
  societyId: { type: Number, default: null },
  eventId: { type: Number, default: null },
  permissions: { type: Array, default: () => [] },
})
const emit = defineEmits(['close', 'linked'])
const query = ref('')
const candidates = ref([])
const total = ref(0)
const truncated = ref(false)
const busy = ref(false)
const loading = ref(false)
const error = ref('')
const selected = ref(null)
let timer = null
let requestVersion = 0
const canLink = computed(() => props.societyId
  ? props.permissions.includes('events.bookings.confirm')
  : can('events.bookings.confirm'))

async function search() {
  const version = ++requestVersion
  loading.value = true
  error.value = ''
  try {
    const data = await api.get(`${props.societyId ? `/society-portal/${props.societyId}/events/${props.eventId}/product-sales` : '/admin/product-sales'}/rows/${props.sale.id}/candidates`, {
      q: query.value.trim(), limit: 60,
    })
    if (version !== requestVersion) return
    candidates.value = data.candidates || []
    total.value = data.total || 0
    truncated.value = data.truncated || false
  } catch (e) {
    if (version === requestVersion) error.value = e.message
  } finally {
    if (version === requestVersion) loading.value = false
  }
}
function queueSearch() {
  if (timer) clearTimeout(timer)
  timer = setTimeout(search, 250)
}
watch(() => props.sale.id, () => {
  query.value = ''
  selected.value = null
  if (timer) clearTimeout(timer)
  search()
}, { immediate: true })
onBeforeUnmount(() => { if (timer) clearTimeout(timer); ++requestVersion })

async function link(row) {
  if (!row.quantity_matches || !canLink.value) return
  const message = `Link reservation #${row.id} (${row.name}, ${row.email}, ${row.quantity} ticket(s)) to Union purchase ${props.sale.order_number || '(no order number)'}?\n\nA held reservation will be confirmed; an already-confirmed reservation stays confirmed. This link cannot be undone by searching again. Check the person and quantity carefully.`
  if (!confirm(message)) return
  busy.value = true
  error.value = ''
  try {
    await api.post(`${props.societyId ? `/society-portal/${props.societyId}/events/${props.eventId}/product-sales` : '/admin/product-sales'}/rows/${props.sale.id}/approve`, { reservation_id: row.id })
    emit('linked', row)
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}
</script>

<template>
  <section class="match-explorer" :aria-label="`Find reservation for imported sale ${sale.id}`">
    <header class="match-head">
      <div>
        <h3>Link a Union purchase to a reservation</h3>
        <p class="match-muted">Purchase <strong>{{ sale.order_number || `row #${sale.id}` }}</strong>
          · {{ sale.buyer_name || 'Unnamed buyer' }} · CID {{ sale.cid || 'not supplied' }}
          · {{ sale.ticket_type_name }} · {{ sale.quantity }} ticket(s)
        </p>
      </div>
      <Button size="sm" variant="ghost" :disabled="busy" @click="emit('close')">Close</Button>
    </header>
    <label class="match-search">
      Search event reservations by name (including first or surname), email, CID, order number or reservation ID
      <input v-model="query" type="search" placeholder="Name, email, CID or reservation #" maxlength="120"
        autofocus :disabled="busy" @input="queueSearch" @keyup.enter="search" />
    </label>
    <p class="match-muted">Held and already-confirmed reservations of this exact ticket type appear. Suggested names are ranked for convenience, not automatically linked. You must choose the correct reservation.</p>
    <p v-if="loading" class="match-muted" role="status">Searching reservations…</p>
    <p v-if="error" class="match-error" role="alert">{{ error }}</p>
    <div v-if="!loading && candidates.length" class="match-results">
      <div v-for="r in candidates" :key="r.id" class="match-result">
        <div class="match-person">
          <strong>{{ r.name }}</strong>
          <span>{{ r.email }}</span>
          <span>Reservation #{{ r.id }} · {{ r.quantity }} ticket(s) · {{ r.cid ? `CID ${r.cid}` : 'no CID' }}</span>
          <span>{{ r.status === 'confirmed' ? 'Already confirmed' : 'Awaiting confirmation' }}</span>
          <span v-if="r.order_number">Existing order {{ r.order_number }}</span>
          <span v-if="r.cid_match || r.email_match || r.score >= 0.65" class="match-score">
            {{ r.cid_match ? 'Exact CID' : r.email_match ? 'Exact email' : `${Math.round(r.score * 100)}% name similarity` }}
          </span>
          <span v-if="!r.quantity_matches" class="match-error">Quantity differs: cannot link</span>
        </div>
        <Button v-if="canLink" size="sm" :disabled="busy || !r.quantity_matches"
          @click="link(r)">{{ r.status === 'confirmed' ? 'Link confirmed ticket' : 'Link ticket' }}</Button>
        <span v-else class="match-muted">Confirmation permission required</span>
      </div>
    </div>
    <p v-if="!loading && !candidates.length && !error" class="match-muted">No eligible reservations found. Try their surname, email, CID or reservation number. Alternatively issue a new ticket if the purchase has no reservation.</p>
    <p v-if="!loading && candidates.length" class="match-muted">Showing {{ candidates.length }} of {{ total }} matches{{ truncated ? ' (search capped at 1,000 reservations)' : '' }}. Narrow the search to find another booking.</p>
  </section>
</template>

<style scoped>
.match-explorer { border:2px solid var(--text); background:var(--surface, #fff); padding:var(--space-5); display:grid; gap:var(--space-4); min-width:0; }
.match-head { display:flex; justify-content:space-between; gap:var(--space-3); align-items:flex-start; }
.match-head h3 { margin:0 0 var(--space-2); font:var(--type-h4); }
.match-muted { margin:0; color:var(--text-muted); font:var(--type-small); }
.match-search { display:grid; gap:var(--space-2); font:var(--type-small); }
.match-search input { padding:var(--space-3); min-height:44px; background:var(--surface, #fff); border:1px solid var(--text); color:var(--text); font:var(--type-body); width:100%; }
.match-results { display:grid; border-top:1px solid var(--border-hairline); max-height:560px; overflow-y:auto; }
.match-result { display:flex; gap:var(--space-4); justify-content:space-between; align-items:center; flex-wrap:wrap; padding:var(--space-3) 0; border-bottom:1px solid var(--border-hairline); }
.match-person { display:grid; gap:var(--space-1); font:var(--type-small); overflow-wrap:anywhere; min-width:0; }
.match-person span:not(.match-error):not(.match-score) { color:var(--text-muted); }
.match-score { font-weight:700; }
.match-error { color:var(--danger); font:var(--type-small); }
@media(max-width:620px) { .match-explorer { padding:var(--space-3); } .match-result { display:grid; } }
</style>
