<script setup>
// Public opportunities, authenticated submissions and the submitter's review
// history. The API is authoritative: no pending content is ever public.
import { computed, inject, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '@/api'
import Badge from '@/components/ds/Badge.vue'
import Button from '@/components/ds/Button.vue'
import Card from '@/components/ds/Card.vue'
import Field from '@/components/ds/Field.vue'
import Input from '@/components/ds/Input.vue'
import TraceRule from '@/components/ds/TraceRule.vue'
import session from '@/stores/session'

const route = useRoute()
const router = useRouter()
const openLogin = inject('openLogin', () => {})
const years = [
  ['1', 'Year 1'], ['2', 'Year 2'], ['3', 'Year 3'], ['4', 'Year 4'],
  ['msc', 'MSc'], ['phd', 'PhD'],
]
const yearName = (id) => years.find(([key]) => key === id)?.[1] || id
const todayLondon = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Europe/London' }).format(new Date())
const items = ref([])
const mine = ref([])
const loading = ref(true)
const mineLoading = ref(false)
const error = ref('')
const formError = ref('')
const submitted = ref(false)
const posting = ref(false)
const withdrawingId = ref(null)
const withdrawNotice = ref('')
const view = ref('list')
const filter = ref('')
const imageFiles = ref([])
const existingImages = ref([])
const editingId = ref(null)
const editingNotes = ref('')
const editingWasApproved = ref(false)
const editingAnnouncement = ref(null)
const draft = reactive({ subject: '', body: '' })
const draftBusy = ref(false)
const draftNotice = ref('')
const opportunity = ref(null)
const form = reactive({
  title: '', description: '', links: [''], contact_email: '',
  contact_phone: '', target_years: [], posted_until: '',
})
const detailId = computed(() => {
  const raw = route.params.id
  return /^\d+$/.test(String(raw || '')) ? Number(raw) : null
})
const filtered = computed(() => {
  const q = filter.value.trim().toLowerCase()
  return !q ? items.value : items.value.filter((o) =>
    `${o.title} ${o.description} ${o.targetYears.map(yearName).join(' ')}`
      .toLowerCase().includes(q),
  )
})

function date(value) {
  if (!value) return ''
  return new Date(value).toLocaleDateString('en-GB', {
    day: 'numeric', month: 'short', year: 'numeric',
  })
}

async function loadPublished() {
  error.value = ''
  loading.value = true
  try {
    items.value = (await api.get('/opportunities')).opportunities
    if (detailId.value) {
      opportunity.value = await api.get(`/opportunities/${detailId.value}`)
    }
  } catch (e) {
    error.value = e.message
    opportunity.value = null
  } finally {
    loading.value = false
  }
}

async function loadMine() {
  if (!session.authenticated) {
    mine.value = []
    return
  }
  mineLoading.value = true
  try {
    mine.value = (await api.get('/me/opportunities')).opportunities
  } catch (e) {
    formError.value = e.message
  } finally {
    mineLoading.value = false
  }
}

function startSubmit() {
  if (!session.authenticated) {
    openLogin()
    return
  }
  submitted.value = false
  draftNotice.value = ''
  editingId.value = null
  editingNotes.value = ''
  editingWasApproved.value = false
  imageFiles.value = []
  existingImages.value = []
  Object.assign(form, {
    title: '', description: '', links: [''], contact_email: '',
    contact_phone: '', target_years: [], posted_until: '',
  })
  formError.value = ''
  view.value = 'submit'
}

function chooseImages(event) {
  const files = Array.from(event.target.files || [])
  if (files.length + existingImages.value.length > 5 || files.some((file) => !['image/jpeg', 'image/png', 'image/webp', 'image/gif'].includes(file.type) || file.size > 10 * 1024 * 1024)) {
    formError.value = 'Choose up to five JPEG, PNG, WebP or GIF images, each under 10 MB.'
    imageFiles.value = []
    event.target.value = ''
    return
  }
  formError.value = ''
  imageFiles.value = files
}

function csrfToken() {
  const item = document.cookie.split('; ').find((part) => part.startsWith('csrf='))
  return item ? decodeURIComponent(item.slice(5)) : ''
}

async function uploadImage(file) {
  const body = new FormData()
  body.append('file', file)
  const token = csrfToken()
  const response = await fetch('/api/opportunities/images', {
    method: 'POST', credentials: 'same-origin',
    headers: token ? { 'X-CSRF-Token': token } : {},
    body,
  })
  const result = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Image upload failed')
  return result.id
}

async function submit() {
  if (!session.authenticated) { openLogin(); return }
  formError.value = ''
  const urls = form.links.map((link) => link.trim()).filter(Boolean)
  if (!form.target_years.length) {
    formError.value = 'Choose at least one target year group.'
    return
  }
  for (const link of urls) {
    try {
      if (!['http:', 'https:'].includes(new URL(link).protocol)) throw new Error()
    } catch {
      formError.value = 'Links need to begin with https:// or http://.'
      return
    }
  }
  posting.value = true
  try {
    const ids = existingImages.value.map((image) => image.id)
    for (const file of imageFiles.value) ids.push(await uploadImage(file))
    const payload = {
      title: form.title.trim(),
      description: form.description.trim(),
      links: urls,
      contact_email: form.contact_email.trim() || null,
      contact_phone: form.contact_phone.trim() || null,
      target_years: form.target_years,
      posted_until: form.posted_until || null,
      image_ids: ids,
    }
    if (editingId.value) {
      await api.put(`/me/opportunities/${editingId.value}`, payload)
    } else {
      await api.post('/opportunities', payload)
    }
    editingId.value = null
    existingImages.value = []
    Object.assign(form, {
      title: '', description: '', links: [''], contact_email: '',
      contact_phone: '', target_years: [], posted_until: '',
    })
    imageFiles.value = []
    submitted.value = true
    view.value = 'mine'
    await loadMine()
  } catch (e) {
    formError.value = e.message
  } finally {
    posting.value = false
  }
}

async function withdrawSubmission(item) {
  if (!session.authenticated || item.status !== 'approved' || withdrawingId.value !== null) return
  if (!confirm(`Take down “${item.title}”? It will immediately disappear from the public page, any pending changes will be cancelled, and this cannot be undone. Your submission history will remain available.`)) return
  withdrawingId.value = item.id
  formError.value = ''
  withdrawNotice.value = ''
  try {
    await api.post(`/me/opportunities/${item.id}/withdraw`)
    withdrawNotice.value = `“${item.title}” has been taken down. It is no longer public.`
    await Promise.all([loadMine(), loadPublished()])
  } catch (e) { formError.value = e.message }
  finally { withdrawingId.value = null }
}

function editSubmission(item) {
  if (!session.authenticated) { openLogin(); return }
  if (!((item.status === 'rejected' && item.allowEdit) || (item.status === 'approved' && !item.pendingRevision))) return
  editingId.value = item.id
  editingWasApproved.value = item.status === 'approved'
  editingNotes.value = item.status === 'rejected' ? item.reviewNotes || '' : ''
  imageFiles.value = []
  existingImages.value = [...item.images]
  Object.assign(form, {
    title: item.title,
    description: item.description,
    links: item.links.length ? [...item.links] : [''],
    contact_email: item.contactEmail || '',
    contact_phone: item.contactPhone || '',
    target_years: [...item.targetYears],
    posted_until: item.postedUntil || '',
  })
  submitted.value = false
  formError.value = ''
  view.value = 'submit'
}

function openDraft(item) {
  if (!session.authenticated || item.status !== 'approved' || item.emailCampaignId) return
  editingAnnouncement.value = item.id
  Object.assign(draft, {
    subject: item.announcementSubject || `Opportunity: ${item.title}`,
    body: item.announcementBody || '',
  })
  draftNotice.value = ''
  formError.value = ''
  view.value = 'draft'
}

async function saveDraft(sendForApproval = false) {
  if (!session.authenticated || !editingAnnouncement.value) return
  draftBusy.value = true
  formError.value = ''
  draftNotice.value = ''
  try {
    const id = editingAnnouncement.value
    await api.put(`/me/opportunities/${id}/announcement`, {
      subject: draft.subject.trim(), body: draft.body.trim(),
    })
    if (sendForApproval) {
      await api.post(`/me/opportunities/${id}/announcement/submit`)
      await loadMine()
      view.value = 'mine'
      draftNotice.value = 'Your announcement is now awaiting final approval by an EESoc admin. It has not been sent.'
    } else {
      await loadMine()
      draftNotice.value = 'Draft saved. You can keep editing it before sending it to admins.'
    }
  } catch (e) {
    formError.value = e.message
  } finally {
    draftBusy.value = false
  }
}

function showMine() {
  if (!session.authenticated) { openLogin(); return }
  formError.value = ''
  view.value = 'mine'
  loadMine()
}

onMounted(() => {
  loadPublished()
  if (session.authenticated) loadMine()
})
watch(() => session.authenticated, (yes) => {
  if (yes) loadMine()
  else { mine.value = []; if (view.value !== 'list') view.value = 'list' }
})
watch(detailId, loadPublished)
</script>

<template>
  <main class="opps">
    <header class="opps__hero">
      <span class="opps__eyebrow">EESoc community</span>
      <h1 class="opps__title">Opportunities</h1>
      <p class="opps__intro">Internships, research, projects, competitions, scholarships and things worth sharing. Submitted by the community, reviewed by EESoc admins.</p>
      <div v-if="!detailId" class="opps__actions">
        <Button :variant="view === 'list' ? 'primary' : 'secondary'" @click="view = 'list'">Browse opportunities</Button>
        <Button :variant="view === 'submit' ? 'primary' : 'secondary'" @click="startSubmit">Submit an opportunity</Button>
        <Button :variant="view === 'mine' ? 'primary' : 'secondary'" @click="showMine">My submissions<template v-if="mine.length"> ({{ mine.length }})</template></Button>
      </div>
      <Button v-else variant="ghost" @click="router.push('/opportunities')">← All opportunities</Button>
    </header>

    <TraceRule class="opps__rule" />
    <p v-if="error" class="opps__error" role="alert">{{ error }}</p>

    <template v-if="detailId">
      <p v-if="loading">Loading opportunity…</p>
      <article v-else-if="opportunity" class="opps__detail">
        <span class="opps__eyebrow">Approved opportunity · {{ date(opportunity.approvedAt) }}</span>
        <p v-if="opportunity.postedUntil" class="opps__meta">Posted until {{ date(opportunity.postedUntil) }}</p>
        <h2 class="opps__title">{{ opportunity.title }}</h2>
        <div class="opps__years"><Badge v-for="year in opportunity.targetYears" :key="year" tone="brand">{{ yearName(year) }}</Badge></div>
        <p class="opps__description">{{ opportunity.description }}</p>
        <div v-if="opportunity.images.length" class="opps__gallery">
          <img v-for="image in opportunity.images" :key="image.id" :src="image.url" :alt="`Image for ${opportunity.title}`" loading="lazy" />
        </div>
        <div v-if="opportunity.links.length" class="opps__link-list">
          <h3>Links</h3>
          <a v-for="(link, i) in opportunity.links" :key="i" :href="link" target="_blank" rel="noopener noreferrer">{{ link }} ↗</a>
        </div>
        <div v-if="opportunity.contactEmail || opportunity.contactPhone" class="opps__link-list">
          <h3>Contact</h3>
          <a v-if="opportunity.contactEmail" :href="`mailto:${opportunity.contactEmail}`">{{ opportunity.contactEmail }}</a>
          <a v-if="opportunity.contactPhone" :href="`tel:${opportunity.contactPhone}`">{{ opportunity.contactPhone }}</a>
        </div>
      </article>
    </template>

    <template v-else-if="view === 'list'">
      <p v-if="loading">Loading opportunities…</p>
      <template v-else>
        <Input v-if="items.length > 3" v-model="filter" placeholder="Search by title, description or year group…" />
        <div v-if="filtered.length" class="opps__grid">
          <Card v-for="item in filtered" :key="item.id" variant="outline" class="opps__listing">
            <img v-if="item.images.length" class="opps__cover" :src="item.images[0].url" :alt="`Image for ${item.title}`" loading="lazy" />
            <div class="opps__listing-body">
              <span class="opps__eyebrow">{{ date(item.approvedAt) }}</span>
              <p v-if="item.postedUntil" class="opps__meta">Posted until {{ date(item.postedUntil) }}</p>
              <h2>{{ item.title }}</h2>
              <div class="opps__years"><Badge v-for="year in item.targetYears" :key="year" tone="brand">{{ yearName(year) }}</Badge></div>
              <p class="opps__excerpt">{{ item.description }}</p>
              <router-link class="opps__link" :to="`/opportunities/${item.id}`">View opportunity ↗</router-link>
            </div>
          </Card>
        </div>
        <Card v-else variant="outline">
          <p class="opps__empty">{{ filter ? 'Nothing matches that search.' : 'No opportunities have been approved yet. Check back soon, or share one with the community.' }}</p>
        </Card>
      </template>
    </template>

    <section v-else-if="view === 'mine'" class="opps__column">
      <h2 class="opps__heading">My submissions</h2>
      <p v-if="submitted" class="opps__success" role="status">Submitted for admin review. You can check its status here.</p>
      <p v-if="draftNotice" class="opps__success" role="status">{{ draftNotice }}</p>
      <p v-if="withdrawNotice" class="opps__success" role="status">{{ withdrawNotice }}</p>
      <p v-if="formError" class="opps__error" role="alert">{{ formError }}</p>
      <p v-if="mineLoading">Loading your submissions…</p>
      <Card v-for="item in mine" :key="item.id" variant="outline">
        <div class="opps__row">
          <h3>{{ item.title }}</h3>
          <Badge :tone="item.status === 'approved' ? 'success' : item.status === 'rejected' ? 'danger' : 'neutral'">{{ item.status === 'withdrawn' ? 'Taken down' : item.status }}</Badge>
        </div>
        <p class="opps__meta">Submitted {{ date(item.createdAt) }} · For {{ item.targetYears.map(yearName).join(', ') }}</p>
        <p class="opps__description">{{ item.description }}</p>
        <p v-if="item.postedUntil" class="opps__meta">Posted until {{ date(item.postedUntil) }}</p>
        <p v-if="item.pendingRevision" class="opps__review"><strong>Updated card awaiting approval.</strong> The currently approved version remains visible to the public until the committee reviews your changes (or its posting deadline passes). Proposed title: {{ item.pendingRevision.title }}<template v-if="item.pendingRevision.postedUntil"> · proposed deadline: {{ date(item.pendingRevision.postedUntil) }}</template>.</p>
        <p v-if="item.status === 'approved' && item.postedUntil && item.postedUntil < todayLondon" class="opps__meta">This opportunity has expired and is no longer publicly listed.</p>
        <p v-if="item.reviewNotes" class="opps__review"><strong>Admin notes:</strong> {{ item.reviewNotes }}</p>
        <p v-if="item.status === 'pending'" class="opps__meta">Waiting for admin review. Only you and the admins can see this.</p>
        <p v-if="item.status === 'withdrawn'" class="opps__meta">Taken down {{ date(item.withdrawnAt) }} by the submitter. This opportunity is no longer public, and its history is retained.</p>
        <template v-if="item.status === 'rejected'">
          <p v-if="item.allowEdit" class="opps__meta">An admin has allowed revisions. Edit the details based on the feedback and submit them for another review.</p>
          <p v-else class="opps__meta">This submission is closed. You can make a new submission instead.</p>
          <div v-if="item.allowEdit"><Button size="sm" variant="secondary" @click="editSubmission(item)">Edit & resubmit</Button></div>
        </template>
        <template v-if="item.status === 'approved'">
          <div class="opps__actions">
            <Button v-if="!item.pendingRevision" size="sm" variant="secondary" :disabled="withdrawingId !== null" @click="editSubmission(item)">Edit listing</Button>
            <Button size="sm" variant="danger" :disabled="withdrawingId !== null" @click="withdrawSubmission(item)">{{ withdrawingId === item.id ? 'Taking down…' : 'Take down' }}</Button>
          </div>
          <router-link v-if="!item.postedUntil || item.postedUntil >= todayLondon" :to="`/opportunities/${item.id}`" class="opps__link">View published listing ↗</router-link>
          <div v-if="!item.emailCampaignId" class="opps__actions">
            <Badge tone="accent">Announcement draft</Badge>
            <Button size="sm" variant="secondary" @click="openDraft(item)">Draft announcement email</Button>
          </div>
          <p v-else class="opps__meta">Announcement submitted to the admins' email approval queue. It will not be sent without final approval.</p>
        </template>
      </Card>
      <Card v-if="!mineLoading && !mine.length" variant="outline"><p class="opps__empty">You have not submitted anything yet.</p></Card>
      <div><Button variant="secondary" @click="loadMine">Refresh status</Button></div>
    </section>

    <section v-else-if="view === 'draft'" class="opps__column">
      <h2 class="opps__heading">Draft your opportunity announcement</h2>
      <p class="opps__intro">Your listing is already published. You can edit the email's subject and message, then approve it for the admins' separate final review. Its recipients will be selected automatically from the opportunity's target year groups; you cannot send it yourself.</p>
      <form class="opps__form" @submit.prevent="saveDraft(false)">
        <Field label="Email subject" required><Input v-model="draft.subject" maxlength="180" required placeholder="Opportunity: Summer research programme" /></Field>
        <Field label="Announcement body" required help="Write a message for the target year groups. Links and the opportunity's description can be included using Markdown.">
          <textarea v-model="draft.body" class="opps__textarea" rows="12" minlength="20" maxlength="10000" required placeholder="Describe what makes the opportunity worth sharing…" />
        </Field>
        <p v-if="formError" class="opps__error" role="alert">{{ formError }}</p>
        <p v-if="draftNotice" class="opps__success" role="status">{{ draftNotice }}</p>
        <div class="opps__actions">
          <Button :disabled="draftBusy" type="submit">{{ draftBusy ? 'Saving…' : 'Save draft' }}</Button>
          <Button :disabled="draftBusy || draft.subject.trim().length < 3 || draft.body.trim().length < 20" variant="secondary" type="button" @click="saveDraft(true)">Approve & send to admins</Button>
          <Button :disabled="draftBusy" variant="ghost" type="button" @click="showMine">Back to submissions</Button>
        </div>
      </form>
    </section>

    <section v-else-if="view === 'submit'" class="opps__column">
      <h2 class="opps__heading">{{ editingId ? (editingWasApproved ? 'Edit published opportunity' : 'Edit rejected opportunity') : 'Submit an opportunity' }}</h2>
      <p class="opps__intro">{{ editingId ? (editingWasApproved ? 'Your proposed changes will go to committee approval. The current approved listing stays public, unchanged, until those changes are approved or its deadline passes.' : 'An admin allowed revisions. Change the details using their notes, then resubmit for a new approval.') : 'You must be signed in. Your submission will not be public until it has been approved by an admin. Contact details you enter will be shown on the public listing if approved.' }}</p>
      <p v-if="editingId && editingNotes" class="opps__review"><strong>Admin feedback to address:</strong> {{ editingNotes }}</p>
      <form class="opps__form" @submit.prevent="submit">
        <Field label="Title" required><Input v-model="form.title" required maxlength="180" placeholder="e.g. Summer research programme" /></Field>
        <Field label="Description" required help="What is it, who is it for, and how can someone take part?">
          <textarea v-model="form.description" required minlength="20" maxlength="10000" rows="7" class="opps__textarea" placeholder="Describe the opportunity, eligibility, dates and next steps…" />
        </Field>
        <Field label="Links" help="Application pages, more information, organiser sites — HTTPS or HTTP only.">
          <div v-for="(link, index) in form.links" :key="index" class="opps__link-row">
            <Input v-model="form.links[index]" type="url" placeholder="https://example.com/apply" />
            <Button v-if="form.links.length > 1" variant="ghost" size="sm" type="button" @click="form.links.splice(index, 1)">Remove</Button>
          </div>
          <Button v-if="form.links.length < 10" variant="ghost" size="sm" type="button" @click="form.links.push('')">+ Add another link</Button>
        </Field>
        <div class="opps__two-col">
          <Field label="Contact email" help="Optional; public after approval."><Input v-model="form.contact_email" type="email" placeholder="organiser@example.com" /></Field>
          <Field label="Contact phone" help="Optional; public after approval."><Input v-model="form.contact_phone" type="tel" maxlength="40" placeholder="+44 …" /></Field>
        </div>
        <Field label="Posted until" help="Optional. The opportunity automatically disappears from the public page after this date (inclusive). You can submit an updated deadline for committee approval.">
          <Input v-model="form.posted_until" type="date" :min="todayLondon" />
        </Field>
        <Field label="Target year groups" required help="Choose all groups who should see this opportunity. These will also be the proposed audiences for the email announcement.">
          <div class="opps__years-choice">
            <label v-for="[id, label] in years" :key="id"><input v-model="form.target_years" type="checkbox" :value="id" /> {{ label }}</label>
          </div>
        </Field>
        <Field label="Images" help="Optional. Up to 5 images total, 10 MB each. JPEG, PNG, WebP or GIF. Uploads occur when you submit.">
          <div v-if="existingImages.length" class="opps__gallery">
            <div v-for="image in existingImages" :key="image.id">
              <img :src="image.url" alt="Previously attached image" loading="lazy" />
              <Button size="sm" variant="ghost" type="button" @click="existingImages = existingImages.filter((img) => img.id !== image.id)">Remove image</Button>
            </div>
          </div>
          <input type="file" accept="image/jpeg,image/png,image/webp,image/gif" multiple @change="chooseImages" />
          <span v-if="imageFiles.length" class="opps__meta">{{ imageFiles.length }} image(s) selected</span>
        </Field>
        <p v-if="formError" class="opps__error" role="alert">{{ formError }}</p>
        <div class="opps__actions">
          <Button :disabled="posting" type="submit">{{ posting ? 'Submitting…' : editingId ? (editingWasApproved ? 'Send changes for approval' : 'Resubmit for approval') : 'Send for approval' }}</Button>
          <Button :disabled="posting" variant="ghost" type="button" @click="view = 'list'">Cancel</Button>
        </div>
      </form>
    </section>
  </main>
</template>

<style scoped>
.opps { max-width: var(--container-lg); margin: 0 auto; padding: var(--space-10) var(--space-6) var(--space-16); min-height: 70vh; }
.opps__hero, .opps__column, .opps__form, .opps__listing-body, .opps__detail { display: grid; gap: var(--space-4); }
.opps__eyebrow { font: var(--type-eyebrow); color: var(--text-faint); letter-spacing: var(--tracking-label); text-transform: uppercase; }
.opps__title { font: var(--type-h1); color: var(--text-strong); margin: 0; }
.opps__heading { font: var(--type-h3); color: var(--text-strong); margin: 0; }
.opps__intro { font: var(--type-body); color: var(--text-muted); line-height: 1.6; max-width: 72ch; margin: 0; }
.opps__rule { margin: var(--space-8) 0; }
.opps__actions, .opps__years, .opps__row { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-3); }
.opps__grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 310px), 1fr)); gap: var(--space-5); margin-top: var(--space-5); }
.opps__listing { overflow: hidden; }
.opps__listing-body { padding: var(--space-2); }
.opps__listing h2, .opps__row h3 { font: var(--type-h4); margin: 0; overflow-wrap: anywhere; }
.opps__row { justify-content: space-between; }
.opps__cover { width: 100%; max-height: 210px; object-fit: cover; border-radius: var(--radius-sm); }
.opps__excerpt { font: var(--type-small); color: var(--text-muted); margin: 0; display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical; overflow: hidden; white-space: pre-line; overflow-wrap: anywhere; }
.opps__description { font: var(--type-body); white-space: pre-line; line-height: 1.7; overflow-wrap: anywhere; }
.opps__meta { font: var(--type-2xs); color: var(--text-faint); margin: 0; }
.opps__link { font: var(--type-small); color: var(--text-accent); text-decoration: underline; overflow-wrap: anywhere; }
.opps__detail { max-width: 850px; }
.opps__gallery { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 230px), 1fr)); gap: var(--space-4); }
.opps__gallery img { max-width: 100%; border-radius: var(--radius-md); }
.opps__link-list { display: grid; gap: var(--space-2); overflow-wrap: anywhere; }
.opps__link-list h3 { margin: 0; font: var(--type-h4); }
.opps__link-list a { font: var(--type-small); color: var(--text-accent); }
.opps__column { max-width: 860px; }
.opps__form { gap: var(--space-6); }
.opps__two-col { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 230px), 1fr)); gap: var(--space-4); }
.opps__textarea { width: 100%; max-width: 100%; padding: var(--space-3); border: 1px solid var(--border-default); border-radius: var(--radius-sm); font: var(--type-body); background: var(--surface-card); color: var(--text-body); }
.opps__link-row { display: flex; gap: var(--space-2); align-items: center; margin: var(--space-2) 0; }
.opps__link-row > :first-child { flex: 1; min-width: 0; }
.opps__years-choice { display: flex; flex-wrap: wrap; gap: var(--space-3); }
.opps__years-choice label { display: inline-flex; gap: 0.4em; align-items: center; font: var(--type-small); cursor: pointer; }
.opps__review { font: var(--type-small); padding: var(--space-3); background: var(--surface-sunken); border-left: 3px solid var(--text-accent); white-space: pre-line; }
.opps__error { font: var(--type-small); color: var(--danger); }
.opps__success { font: var(--type-small); color: var(--text-strong); padding: var(--space-3); border-left: 3px solid var(--text-accent); }
.opps__empty { font: var(--type-small); color: var(--text-muted); margin: 0; }
@media (max-width: 600px) {
  /* Avoid forcing a 425px layout viewport for the unbreakable H1 on phones. */
  .opps__title { font-size: clamp(2rem, 8vw, var(--text-4xl)); }
}
</style>
