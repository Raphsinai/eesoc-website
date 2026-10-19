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
const items = ref([])
const mine = ref([])
const loading = ref(true)
const mineLoading = ref(false)
const error = ref('')
const formError = ref('')
const submitted = ref(false)
const posting = ref(false)
const view = ref('list')
const filter = ref('')
const imageFiles = ref([])
const opportunity = ref(null)
const form = reactive({
  title: '', description: '', links: [''], contact_email: '',
  contact_phone: '', target_years: [],
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
  formError.value = ''
  view.value = 'submit'
}

function chooseImages(event) {
  const files = Array.from(event.target.files || [])
  if (files.length > 5 || files.some((file) => !['image/jpeg', 'image/png', 'image/webp', 'image/gif'].includes(file.type) || file.size > 10 * 1024 * 1024)) {
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
    const ids = []
    for (const file of imageFiles.value) ids.push(await uploadImage(file))
    await api.post('/opportunities', {
      title: form.title.trim(),
      description: form.description.trim(),
      links: urls,
      contact_email: form.contact_email.trim() || null,
      contact_phone: form.contact_phone.trim() || null,
      target_years: form.target_years,
      image_ids: ids,
    })
    Object.assign(form, {
      title: '', description: '', links: [''], contact_email: '',
      contact_phone: '', target_years: [],
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
      <p class="opps__intro">Internships, research, projects, competitions, scholarships and things worth sharing. Submitted by the community, reviewed by the EESoc committee.</p>
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
      <p v-if="submitted" class="opps__success" role="status">Submitted for committee review. You can check its status here.</p>
      <p v-if="formError" class="opps__error" role="alert">{{ formError }}</p>
      <p v-if="mineLoading">Loading your submissions…</p>
      <Card v-for="item in mine" :key="item.id" variant="outline">
        <div class="opps__row">
          <h3>{{ item.title }}</h3>
          <Badge :tone="item.status === 'approved' ? 'success' : item.status === 'rejected' ? 'danger' : 'accent'">{{ item.status }}</Badge>
        </div>
        <p class="opps__meta">Submitted {{ date(item.createdAt) }} · For {{ item.targetYears.map(yearName).join(', ') }}</p>
        <p class="opps__description">{{ item.description }}</p>
        <p v-if="item.reviewNotes" class="opps__review"><strong>Committee notes:</strong> {{ item.reviewNotes }}</p>
        <p v-if="item.status === 'pending'" class="opps__meta">Waiting for committee review. Only you and the committee can see this.</p>
        <router-link v-if="item.status === 'approved'" :to="`/opportunities/${item.id}`" class="opps__link">View published listing ↗</router-link>
      </Card>
      <Card v-if="!mineLoading && !mine.length" variant="outline"><p class="opps__empty">You have not submitted anything yet.</p></Card>
      <div><Button variant="secondary" @click="loadMine">Refresh status</Button></div>
    </section>

    <section v-else-if="view === 'submit'" class="opps__column">
      <h2 class="opps__heading">Submit an opportunity</h2>
      <p class="opps__intro">You must be signed in. Your submission will not be public until it has been approved by the committee. Contact details you enter will be shown on the public listing if approved.</p>
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
        <Field label="Target year groups" required help="Choose all groups who should see this opportunity. These will also be the proposed audiences for the email announcement.">
          <div class="opps__years-choice">
            <label v-for="[id, label] in years" :key="id"><input v-model="form.target_years" type="checkbox" :value="id" /> {{ label }}</label>
          </div>
        </Field>
        <Field label="Images" help="Optional. Up to 5 images, 10 MB each. JPEG, PNG, WebP or GIF. Uploads occur when you submit.">
          <input type="file" accept="image/jpeg,image/png,image/webp,image/gif" multiple @change="chooseImages" />
          <span v-if="imageFiles.length" class="opps__meta">{{ imageFiles.length }} image(s) selected</span>
        </Field>
        <p v-if="formError" class="opps__error" role="alert">{{ formError }}</p>
        <div class="opps__actions">
          <Button :disabled="posting" type="submit">{{ posting ? 'Submitting…' : 'Send for approval' }}</Button>
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
.opps__two-col { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: var(--space-4); }
.opps__textarea { width: 100%; max-width: 100%; padding: var(--space-3); border: 1px solid var(--border-default); border-radius: var(--radius-sm); font: var(--type-body); background: var(--surface-card); color: var(--text-body); }
.opps__link-row { display: flex; gap: var(--space-2); align-items: center; margin: var(--space-2) 0; }
.opps__link-row > :first-child { flex: 1; min-width: 0; }
.opps__years-choice { display: flex; flex-wrap: wrap; gap: var(--space-3); }
.opps__years-choice label { display: inline-flex; gap: 0.4em; align-items: center; font: var(--type-small); cursor: pointer; }
.opps__review { font: var(--type-small); padding: var(--space-3); background: var(--surface-sunken); border-left: 3px solid var(--text-accent); white-space: pre-line; }
.opps__error { font: var(--type-small); color: var(--danger); }
.opps__success { font: var(--type-small); color: var(--text-strong); padding: var(--space-3); border-left: 3px solid var(--text-accent); }
.opps__empty { font: var(--type-small); color: var(--text-muted); margin: 0; }
</style>
