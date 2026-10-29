<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import api from '@/api'
import session from '@/stores/session'
import Button from '@/components/ds/Button.vue'
import Card from '@/components/ds/Card.vue'
import Field from '@/components/ds/Field.vue'
import Input from '@/components/ds/Input.vue'

const entries = ref([])
const societyId = ref(null)
const events = ref([])
const selectedEvent = ref(null)
const eventDraft = ref({})
const ticketDrafts = ref([])
const newTicket = ref({name:'',price_pence:0,capacity:null,max_per_user:1,requires_purchase:false})
const audiences = ref([])
const selectedAudiences = ref([])
const subject = ref('')
const heading = ref('')
const body = ref('')
const preview = ref(null)
const whatsapp = ref(null)
const invite = ref('')
const phone = ref('')
const inviteRequests = ref([])
const busy = ref(false)
const error = ref('')
const notice = ref('')
const page = ref('events')
const society = computed(() => entries.value.find(s => s.id === societyId.value))
const may = action => (society.value?.permissions || []).includes(action)
const canManage = action => Boolean(society.value?.managing && may(action))
const currentEvent = computed(() => events.value.find(e => e.id === selectedEvent.value))
const inputTime = value => value ? new Date(value).toISOString().slice(0,16) : ''
const isoTime = value => value ? new Date(value).toISOString() : null

async function load() {
  error.value=''
  if (!session.authenticated) { entries.value=[]; return }
  try {
    entries.value=await api.get('/society-portal')
    if (!entries.value.some(s => s.id === societyId.value)) societyId.value=entries.value[0]?.id || null
    await loadSociety()
  } catch (e) { error.value=e.message }
}
async function loadSociety() {
  events.value=[]; audiences.value=[]; whatsapp.value=null; selectedEvent.value=null
  if (!societyId.value) return
  const prefix=`/society-portal/${societyId.value}`
  try {
    if (may('events.view')) events.value=await api.get(`${prefix}/events`)
    if (may('announcements.preview')) audiences.value=(await api.get(`${prefix}/announcements/audiences`)).audiences || []
    if (may('whatsapp.view')) {
      whatsapp.value=await api.get(`${prefix}/whatsapp`)
      inviteRequests.value=await api.get(`${prefix}/whatsapp/invites`)
    }
  } catch(e) { error.value=e.message }
}
async function act(task,done) {
  busy.value=true;error.value='';notice.value=''
  try { await task();notice.value=done;await loadSociety() }
  catch(e) { error.value=e.message }
  finally { busy.value=false }
}
function editEvent(id) {
  const ev=events.value.find(x => x.id===id)
  selectedEvent.value=id
  ticketDrafts.value=(ev.ticket_types || []).map(t => ({...t}))
  eventDraft.value={title:ev.title,description:ev.description || '',venue:ev.venue || '',
    starts_at:inputTime(ev.starts_at),ends_at:inputTime(ev.ends_at),
    published:ev.published}
}
function saveEvent() {
  const id=selectedEvent.value, d=eventDraft.value
  act(() => api.put(`/society-portal/${societyId.value}/events/${id}`,{
    title:d.title,description:d.description,venue:d.venue,
    starts_at:isoTime(d.starts_at),ends_at:isoTime(d.ends_at),
  }),'Event saved.')
}
function saveTicket(ticket) {
  const id=selectedEvent.value
  const fields=['name','description','price_pence','capacity','requires_purchase','max_per_user','sort_order','purchase_url','sales_open_at','sales_close_at']
  const payload=Object.fromEntries(fields.filter(k => ticket[k] !== undefined).map(k => [k,ticket[k]]))
  act(() => api.put(`/society-portal/${societyId.value}/events/${id}/tickets/${ticket.id}`,payload),'Ticket type updated.')
}
function createTicket() {
  const id=selectedEvent.value
  act(() => api.post(`/society-portal/${societyId.value}/events/${id}/tickets`,newTicket.value),'Ticket type created.')
}
function removeTicket(id) {
  if (!confirm('Delete this ticket type? Existing reservations may prevent removal.')) return
  act(() => api.del(`/society-portal/${societyId.value}/events/${selectedEvent.value}/tickets/${id}`),'Ticket type deleted.')
}
function publishEvent() {
  const ev=currentEvent.value
  if (!confirm(`${ev?.published ? 'Unpublish' : 'Publish'} this event?`)) return
  act(() => api.patch(`/society-portal/${societyId.value}/events/${ev.id}/published`,{published:!ev.published}), 'Event visibility updated.')
}
async function showPreview() {
  busy.value=true;error.value=''
  try {
    preview.value=await api.post(`/society-portal/${societyId.value}/announcements/preview`,{
      subject:subject.value,heading:heading.value,body:body.value,audiences:selectedAudiences.value,
      event_id:selectedEvent.value || null,
    })
  } catch(e) { error.value=e.message }
  finally { busy.value=false }
}
function send() {
  if (!confirm(`Send this announcement to ${selectedAudiences.value.length} selected audience(s)?`)) return
  act(() => api.post(`/society-portal/${societyId.value}/announcements/send`,{
    subject:subject.value,heading:heading.value,body:body.value,audiences:selectedAudiences.value,
    event_id:selectedEvent.value || null,
  }), 'Announcement queued for delivery.')
}
function linkWhatsApp() {
  act(async () => { await api.post(`/society-portal/${societyId.value}/whatsapp/link`, phone.value ? {phone:phone.value} : {}) },'Started WhatsApp linking; scan the QR or use the pairing code.')
}
function unlinkWhatsApp() {
  if (!confirm('Unlink your society’s WhatsApp account?')) return
  act(() => api.post(`/society-portal/${societyId.value}/whatsapp/unlink`),'WhatsApp account unlinked.')
}
function requestJoin() {
  act(async () => { await api.post(`/society-portal/${societyId.value}/whatsapp/invite`,{invite:invite.value});invite.value='' },'Invitation submitted to the EESoc committee for approval.')
}
function toggleGroup(group) {
  act(() => api.put(`/society-portal/${societyId.value}/whatsapp/groups/${encodeURIComponent(group.jid)}`,{enabled:!group.enabled}), 'Group visibility updated.')
}
watch(() => session.authenticated,load)
onMounted(load)
</script>

<template>
  <main class="society-portal">
    <div class="society-portal__inner">
      <h1>Society portal</h1>
      <p v-if="!session.authenticated">Sign in with your Imperial account to access your society's committee tools.</p>
      <template v-else>
        <p v-if="error" style="color:var(--danger)">{{ error }}</p>
        <p v-if="notice">{{ notice }}</p>
        <p v-if="!entries.length">No society committee assignment is linked to your Imperial login.</p>
        <template v-else>
          <Field label="Society"><select v-model="societyId" class="ees-select" @change="loadSociety"><option v-for="s in entries" :key="s.id" :value="s.id">{{ s.name }}</option></select></Field>
          <p style="font:var(--type-small);color:var(--text-muted)">{{ society?.title }} · {{ society?.managing ? 'Managing committee' : 'Committee' }}. Access is restricted to the actions you have been granted.</p>
          <div style="display:flex;gap:var(--space-2);margin:var(--space-4) 0;flex-wrap:wrap">
            <Button v-if="may('events.view')" :variant="page==='events' ? 'primary':'secondary'" @click="page='events'">Your events</Button>
            <Button v-if="may('announcements.preview')" :variant="page==='announcements' ? 'primary':'secondary'" @click="page='announcements'">Announcements</Button>
            <Button v-if="may('whatsapp.view')" :variant="page==='whatsapp' ? 'primary':'secondary'" @click="page='whatsapp'">WhatsApp</Button>
          </div>
          <div v-if="page==='events' && may('events.view')" style="display:grid;gap:var(--space-4)">
            <Card variant="outline"><h2>Assigned events</h2>
              <p v-if="!events.length">No events have been assigned to your society.</p>
              <div v-for="ev in events" :key="ev.id" style="display:flex;gap:var(--space-3);justify-content:space-between;border-top:1px solid var(--border-hairline);padding:var(--space-2) 0">
                <span>{{ ev.title }} · {{ ev.published ? 'Published':'Draft' }}</span>
                <Button size="sm" variant="secondary" @click="editEvent(ev.id)">Open</Button>
              </div>
            </Card>
            <Card v-if="currentEvent" variant="outline"><h2>{{ currentEvent.title }}</h2>
              <template v-if="canManage('events.edit')">
                <Field label="Name"><Input v-model="eventDraft.title" /></Field>
                <Field label="Description"><textarea v-model="eventDraft.description" class="ees-textarea" rows="5" /></Field>
                <Field label="Venue"><Input v-model="eventDraft.venue" /></Field>
                <Field label="Starts"><input v-model="eventDraft.starts_at" class="ees-input" type="datetime-local" /></Field>
                <Field label="Ends"><input v-model="eventDraft.ends_at" class="ees-input" type="datetime-local" /></Field>
                <Button :disabled="busy" @click="saveEvent">Save event</Button>
              </template>
              <Button v-if="canManage('events.publish')" variant="secondary" :disabled="busy" @click="publishEvent">{{ currentEvent.published ? 'Unpublish':'Publish' }} event</Button>
              <div v-if="canManage('events.tickets.edit') || canManage('events.tickets.create') || canManage('events.tickets.delete')" style="display:grid;gap:var(--space-3);border-top:1px solid var(--border-hairline);padding-top:var(--space-3)">
                <h3>Ticket types</h3>
                <div v-for="ticket in ticketDrafts" :key="ticket.id" style="display:grid;gap:var(--space-2);border-top:1px solid var(--border-hairline);padding:var(--space-3) 0">
                  <Field label="Ticket name"><Input v-model="ticket.name" :disabled="!canManage('events.tickets.edit')" /></Field>
                  <div style="display:flex;gap:var(--space-2)">
                    <Field label="Price (pence)"><input v-model.number="ticket.price_pence" class="ees-input" type="number" min="0" :disabled="!canManage('events.tickets.edit')" /></Field>
                    <Field label="Capacity"><input v-model.number="ticket.capacity" class="ees-input" type="number" min="1" :disabled="!canManage('events.tickets.edit')" /></Field>
                  </div>
                  <label><input v-model="ticket.requires_purchase" type="checkbox" :disabled="!canManage('events.tickets.edit')" /> Requires SUMS purchase</label>
                  <p v-if="ticket.members_only || ticket.eligible_society_ids?.length" style="font:var(--type-small);color:var(--text-muted)">Membership restrictions are set by EESoc administrators and cannot be altered from this portal.</p>
                  <div style="display:flex;gap:var(--space-2)"><Button v-if="canManage('events.tickets.edit')" size="sm" :disabled="busy" @click="saveTicket(ticket)">Save ticket</Button><Button v-if="canManage('events.tickets.delete')" size="sm" variant="ghost" :disabled="busy" @click="removeTicket(ticket.id)">Delete</Button></div>
                </div>
                <div v-if="canManage('events.tickets.create')" style="display:grid;gap:var(--space-2)">
                  <h4>Add ticket type</h4><Field label="Name"><Input v-model="newTicket.name" /></Field>
                  <Field label="Price (pence)"><input v-model.number="newTicket.price_pence" class="ees-input" type="number" min="0" /></Field>
                  <label><input v-model="newTicket.requires_purchase" type="checkbox" /> Requires SUMS purchase</label>
                  <Button size="sm" :disabled="busy || !newTicket.name.trim()" @click="createTicket">Add ticket type</Button>
                </div>
              </div>
              <div v-if="canManage('events.bookings.view')"><h3>Bookings</h3><p>Only committee members with booking access can view the attendance list.</p><Button size="sm" variant="secondary" @click="act(async()=>{ const x=await api.get(`/society-portal/${societyId}/events/${currentEvent.id}/bookings`);notice=`${x.length} booking records` },'Bookings checked')">Count bookings</Button></div>
            </Card>
          </div>
          <Card v-if="page==='announcements' && may('announcements.preview')" variant="outline">
            <h2>Society announcements</h2>
            <p style="font:var(--type-small);color:var(--text-muted)">Only the test audience, your society’s synced membership list and WhatsApp groups assigned to your society are available.</p>
            <Field label="Subject"><Input v-model="subject" /></Field>
            <Field label="Heading"><Input v-model="heading" /></Field>
            <Field label="Message"><textarea v-model="body" class="ees-textarea" rows="8" /></Field>
            <Field label="For assigned event (optional)"><select v-model="selectedEvent" class="ees-select"><option :value="null">General announcement</option><option v-for="ev in events" :key="ev.id" :value="ev.id">{{ ev.title }}</option></select></Field>
            <div style="display:grid;gap:var(--space-2)"><label v-for="a in audiences" :key="a.key"><input v-model="selectedAudiences" type="checkbox" :value="a.key" /> {{ a.label }} ({{ a.count ?? '—' }})</label></div>
            <div style="display:flex;gap:var(--space-2)"><Button :disabled="busy" variant="secondary" @click="showPreview">Preview</Button><Button v-if="canManage('announcements.send') || canManage('whatsapp.post')" :disabled="busy || !selectedAudiences.length" @click="send">Send announcement</Button></div>
            <div v-if="preview"><p>Recipients: {{ preview.recipients }} · WhatsApp groups: {{ preview.groups?.length || 0 }}</p><div v-html="preview.html" /></div>
          </Card>
          <Card v-if="page==='whatsapp' && may('whatsapp.view')" variant="outline">
            <h2>Society WhatsApp</h2>
            <p>Connection: {{ whatsapp?.status?.state || 'Not configured' }}</p>
            <p v-if="whatsapp?.status?.error">{{ whatsapp.status.error }}</p>
            <template v-if="whatsapp?.status?.state==='linking'"><img v-if="whatsapp.status.qr" :src="whatsapp.status.qr" alt="WhatsApp linking QR" style="width:200px" /><p v-if="whatsapp.status.pairingCode">Pairing code: {{ whatsapp.status.pairingCode }}</p><Button size="sm" variant="secondary" @click="loadSociety">Refresh linking status</Button></template>
            <div v-if="canManage('whatsapp.link')"><Field label="Phone number for pairing code (optional)"><Input v-model="phone" /></Field><Button :disabled="busy" size="sm" @click="linkWhatsApp">Link society number</Button><Button :disabled="busy" size="sm" variant="ghost" @click="unlinkWhatsApp">Unlink</Button></div>
            <h3>Linked groups</h3><p v-if="!whatsapp?.groups?.length">No groups found.</p>
            <div v-for="group in whatsapp?.groups || []" :key="group.jid" style="display:flex;gap:var(--space-2);justify-content:space-between;padding:var(--space-2) 0;border-top:1px solid var(--border-hairline)"><span>{{ group.name }} · {{ group.enabled ? 'Enabled':'Disabled' }}</span><Button v-if="canManage('whatsapp.groups.manage')" size="sm" variant="secondary" @click="toggleGroup(group)">{{ group.enabled ? 'Disable':'Enable' }}</Button></div>
            <template v-if="canManage('whatsapp.group.request')"><h3>Invite EESoc to a group instead</h3><p style="font:var(--type-small)">If your society does not have a WhatsApp account, share an invite to a group you administer. An EESoc administrator must approve joining before announcements can be sent.</p><Field label="WhatsApp group invite link"><Input v-model="invite" placeholder="https://chat.whatsapp.com/..." /></Field><Button :disabled="busy || !invite.trim()" @click="requestJoin">Request EESoc to join</Button></template>
            <div v-for="request in inviteRequests" :key="request.id">Invite #{{ request.id }} · {{ request.status }}</div>
          </Card>
        </template>
      </template>
    </div>
  </main>
</template>

<style scoped>
.society-portal{padding:var(--space-8) var(--space-4);min-height:60vh}
.society-portal__inner{max-width:940px;margin:0 auto;display:grid;gap:var(--space-4)}
</style>
