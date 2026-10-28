<script setup>
// Mobile photo booth: Sony -> iPhone -> EESoc -> AirPrint/SELPHY.
// One photo per request avoids the nginx 12 MB body cap with four Sony originals.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import api from '@/api'
import { can } from '@/stores/session'
import Button from '@/components/ds/Button.vue'
import Card from '@/components/ds/Card.vue'
import BoothPrintEditor from '@/components/BoothPrintEditor.vue'

const selections = ref([])
const events = ref([])
const selectedEventId = ref(null)
const shortName = ref('')
const savingShortName = ref(false)
const eventsLoading = ref(false)
const selectedEvent = computed(() =>
  events.value.find(event => event.id === selectedEventId.value) || null)
watch(selectedEventId, () => {
  shortName.value = selectedEvent.value?.photoShortName || ''
})

const framing = ref([])
const framingChanged = computed(() => Boolean(active.value && JSON.stringify(framing.value) !==
  JSON.stringify(active.value.images.map((_,i) => ({x:active.value.placements?.[i]?.x ?? .5,
    y:active.value.placements?.[i]?.y ?? .5, zoom:active.value.placements?.[i]?.zoom ?? 1})))))
const activePhotos = computed(() => active.value?.images?.map((image,i) => ({
  url:image.url, ...framing.value[i],
})) || [])
function eventDate(value) {
  if (!value) return ''
  const date = new Date(value)
  const parts = new Intl.DateTimeFormat('en-GB', {timeZone:'Europe/London', weekday:'short', day:'numeric', month:'short', year:'numeric'}).formatToParts(date)
  const find = type => parts.find(part => part.type===type)?.value || ''
  return `${find('weekday')} ${find('day')} ${find('month')} ${find('year')}`
}
function updateSelectionFraming(next) {
  if (busy.value) return
  selections.value = selections.value.map((entry,i) => ({...entry,...next[i]}))
}
function updateDraftFraming(next) {framing.value = next}
async function saveFraming() {
  if (!active.value || active.value.status !== 'draft' || !can('photos.framing.edit') || !framingChanged.value || busy.value) return
  busy.value = true;error.value = '';notice.value = ''
  try {
    active.value = await api.patch(`/admin/photos/sets/${active.value.id}/framing`,{placements:framing.value})
    framing.value = active.value.images.map((_,i)=>({x:active.value.placements?.[i]?.x??.5, y:active.value.placements?.[i]?.y??.5, zoom:active.value.placements?.[i]?.zoom??1}))
    await Promise.all([makePreview(),loadSets()])
    notice.value = 'Photo crop and zoom saved to this draft.'
  } catch(e) {error.value = e.message} finally {busy.value = false}
}
const busy = ref(false)
const error = ref('')
const notice = ref('')
const progress = ref('')
const sets = ref([])
const active = ref(null)
const previewUrl = ref('')
const printFile = ref(null)

const canStart = computed(() => can('photos.view') && can('photos.upload') && can('photos.create'))
const canCreatePrint = computed(() => canStart.value && !!selectedEvent.value)

const printUrl = computed(() => !active.value ? '' :
  active.value.status === 'published'
    ? active.value.printUrl
    : `/api/admin/photos/sets/${active.value.id}/preview.png`)

function cleanupSelections() {
  for (const entry of selections.value) URL.revokeObjectURL(entry.url)
  selections.value = []
}
function resetPrintPreview() {
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
  previewUrl.value = ''
  printFile.value = null
}

onBeforeUnmount(() => {
  cleanupSelections()
  resetPrintPreview()
})

function pick(event) {
  error.value = ''
  const files = Array.from(event.target.files || [])
  for (const file of files) {
    if (selections.value.length === 4) break
    if (!file.type.startsWith('image/')) {
      error.value = 'Select JPEG, PNG, WebP or iPhone HEIC photos.'
      continue
    }
    selections.value.push({ file, url: URL.createObjectURL(file), x:.5, y:.5, zoom:1 })
  }
  if (files.length > 4 || selections.value.length === 4 && files.length > 4) {
    notice.value = 'Only the first four photos are selected.'
  }
  event.target.value = ''
}
function removePhoto(index) {
  const [entry] = selections.value.splice(index, 1)
  if (entry) URL.revokeObjectURL(entry.url)
}
function movePhoto(index, offset) {
  const other = index + offset
  if (other < 0 || other >= selections.value.length) return
  const next = [...selections.value]
  ;[next[index], next[other]] = [next[other], next[index]]
  selections.value = next
}
async function jpegForUpload(file) {
  // The Sony JPEG is passed through untouched if it fits; the backend strips
  // location EXIF. Browser conversion handles oversize images and iPhone HEIC.
  if (file.type === 'image/jpeg' && file.size < 9_500_000) return file
  const url = URL.createObjectURL(file)
  const image = new Image()
  try {
    const loaded = new Promise((resolve, reject) => {
      image.onload = resolve
      image.onerror = () => reject(new Error(`Cannot decode ${file.name}. Transfer it as JPEG rather than RAW/HEIC.`))
    })
    image.src = url
    await loaded
    const edge = Math.max(image.naturalWidth, image.naturalHeight)
    if (!edge) throw new Error('This image has no dimensions')
    // Keep detail for digital downloads without exhausting Safari's canvas.
    let maxEdge = Math.min(edge, 5200)
    for (let attempt = 0; attempt < 4; attempt++) {
      const scale = Math.min(1, maxEdge / edge)
      const canvas = document.createElement('canvas')
      canvas.width = Math.round(image.naturalWidth * scale)
      canvas.height = Math.round(image.naturalHeight * scale)
      const ctx = canvas.getContext('2d')
      if (!ctx) throw new Error('Unable to process this photo')
      ctx.imageSmoothingQuality = 'high'
      ctx.drawImage(image, 0, 0, canvas.width, canvas.height)
      const quality = attempt === 0 ? 0.90 : 0.84
      const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', quality))
      canvas.width = canvas.height = 1
      if (!blob) throw new Error('Could not convert the selected photo to JPEG')
      if (blob.size < 9_500_000) return new File([blob], 'photo.jpg', { type: 'image/jpeg' })
      maxEdge = Math.round(maxEdge * 0.8)
    }
    throw new Error('This photo is still too large after conversion')
  } finally {
    URL.revokeObjectURL(url)
  }
}

async function loadSets() {
  if (!can('photos.view')) return
  try {
    sets.value = (await api.get('/admin/photos/sets')).sets
  } catch (e) {
    error.value = e.message
  }
}
async function loadEvents() {
  if (!can('photos.view')) return
  eventsLoading.value = true
  try {
    const response = await api.get('/admin/photos/events')
    events.value = response.events || []
    // The server remembers the last print event across committee phones.
    // If it was removed, choose the calendar event closest to today.
    const previous = Number(selectedEventId.value)
    const last = Number(response.lastPrintedEventId)
    if (events.value.some(event => event.id === previous)) return
    if (events.value.some(event => event.id === last)) {
      selectedEventId.value = last
    } else {
      const now = Date.now()
      const byDistance = [...events.value].sort((a, b) =>
        Math.abs(new Date(a.startsAt).getTime()-now) -
        Math.abs(new Date(b.startsAt).getTime()-now))
      selectedEventId.value = byDistance[0]?.id ?? null
    }
    shortName.value = selectedEvent.value?.photoShortName || ''
  } catch (e) {
    error.value = e.message
  } finally {
    eventsLoading.value = false
  }
}

async function saveShortName() {
  const event = selectedEvent.value
  if (!event?.canEditShortName || savingShortName.value) return
  savingShortName.value = true
  error.value = ''
  notice.value = ''
  try {
    const result = await api.patch('/admin/events/' + event.id + '/photo-short-name', {
      photo_short_name: shortName.value.trim() || null,
    })
    events.value = events.value.map(candidate => candidate.id === event.id
      ? { ...candidate, photoShortName: result.photo_short_name } : candidate)
    notice.value = 'Photo short name saved for this event.'
  } catch (e) {
    error.value = e.message
  } finally {
    savingShortName.value = false
  }
}

onMounted(() => {
  loadEvents()
  loadSets()
})

async function openSet(set) {
  active.value = set
  framing.value = set.images.map((_,i)=>({x:set.placements?.[i]?.x??.5,
    y:set.placements?.[i]?.y??.5,zoom:set.placements?.[i]?.zoom??1}))
  resetPrintPreview()
  notice.value = ''
  await makePreview()
}
async function makePreview() {
  if (!active.value) return
  resetPrintPreview()
  try {
    const response = await fetch(printUrl.value, { credentials: 'same-origin' })
    if (!response.ok) throw new Error('Could not generate print preview')
    const blob = await response.blob()
    const filename = (active.value?.printLabel || active.value?.title || 'EESoc')
      .replace(/[^a-z0-9_-]+/gi, '-').slice(0, 55)
    printFile.value = new File([blob], filename + '.png', { type: 'image/png' })
    previewUrl.value = URL.createObjectURL(blob)
  } catch (e) {
    error.value = e.message
  }
}

async function createDraft() {
  if (!canCreatePrint.value || busy.value || !selections.value.length) return
  busy.value = true
  error.value = ''
  notice.value = ''
  const ids = []
  let created = false
  try {
    for (const [index, entry] of selections.value.entries()) {
      progress.value = `Preparing photo ${index+1} of ${selections.value.length}...`
      const file = await jpegForUpload(entry.file)
      const data = new FormData()
      data.append('file', file, 'eesoc-photo.jpg')
      // Use the same shared CSRF cookie as the site's existing file upload.
      const token = document.cookie.split('; ').find(row => row.startsWith('csrf='))?.slice(5) || ''
      progress.value = `Uploading photo ${index+1} of ${selections.value.length}...`
      const res = await fetch('/api/admin/photos/uploads', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'X-CSRF-Token': decodeURIComponent(token) },
        body: data,
      })
      const result = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Photo upload failed')
      ids.push(result.id)
    }
    progress.value = 'Building your photo print...'
    const set = await api.post('/admin/photos/sets', {
      event_id: selectedEventId.value,
      orientation: 'portrait',
      image_ids: ids,
      placements: selections.value.map(({x,y,zoom})=>({x,y,zoom})),
    })
    created = true
    cleanupSelections()
    await loadSets()
    await openSet(set)
    notice.value = 'Draft created. Review it, then publish to activate its QR link.'
  } catch (e) {
    if (!created) {
      await Promise.allSettled(ids.map(id => api.del('/admin/photos/uploads/' + id)))
    }
    error.value = e.message
  } finally {
    busy.value = false
    progress.value = ''
  }
}

async function mutate(set, operation) {
  if (operation === 'publish' && !confirm(
    'Publish these photos? Anyone who scans the print QR will be able to view and download them.'
  )) return
  busy.value = true
  error.value = ''
  try {
    if (operation === 'delete') {
      if (!confirm('Permanently delete this photo set? Its digital download and QR link will stop working.')) return
      await api.del(`/admin/photos/sets/${set.id}`)
      if (active.value?.id === set.id) {
        active.value = null
        resetPrintPreview()
      }
    } else {
      const updated = await api.post(`/admin/photos/sets/${set.id}/${operation}`)
      if (active.value?.id === set.id) {
        active.value = updated
        framing.value = updated.images.map((_,i)=>({x:updated.placements?.[i]?.x??.5,
          y:updated.placements?.[i]?.y??.5,zoom:updated.placements?.[i]?.zoom??1}))
        await makePreview()
      }
    }
    await loadSets()
    notice.value = operation === 'publish' ? 'Published. The QR now opens this photo set.' :
      operation === 'unpublish' ? 'Unpublished. The QR no longer opens the photo set.' :
      'Photo set deleted.'
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}
async function recordPrinted(set) {
  if (!set || !can('photos.print')) return
  // This is an operator print intent, not a hardware receipt.
  const updated = await api.post('/admin/photos/sets/' + set.id + '/printed')
  if (active.value?.id === set.id) active.value = updated
  await loadSets()
  if (set.eventId) {
    selectedEventId.value = set.eventId
    await loadEvents()
  }
  notice.value = 'Print opened. The next set will default to ' + set.title + '.'
}

async function sharePrint() {
  if (!printFile.value || !active.value || !can('photos.print')) return
  const set = active.value
  try {
    if (navigator.canShare?.({ files: [printFile.value] }) && navigator.share) {
      // Trigger the native sheet directly on click, as iOS requires.
      await navigator.share({ files: [printFile.value] })
    } else {
      window.open(printUrl.value, '_blank', 'noopener')
    }
    await recordPrinted(set)
  } catch (e) {
    if (e.name !== 'AbortError') error.value = e.message
  }
}

function openFullSizePrint() {
  // The anchor opens the image in a new tab without losing the operator
  // gesture; record that print path too for cross-device event preselection.
  if (!active.value || !can('photos.print')) return
  recordPrinted(active.value).catch(e => { error.value = e.message })
}
async function shareLink() {
  if (!active.value?.url) return
  try {
    await navigator.clipboard.writeText(active.value.url)
    notice.value = 'Link copied. Anyone with it can view these photos.'
  } catch {
    prompt('Copy the photo page link:', active.value.url)
  }
}
</script>

<template>
  <section class="photo-studio">
    <header class="photo-studio__header">
      <div>
        <span class="eyebrow">EESoc event photography</span>
        <h2>Photo print studio</h2>
        <p class="muted">Shoot on your Sony, transfer to your phone, then make a QR-linked 6×4 print for the Canon SELPHY CP1500.</p>
      </div>
    </header>

    <p v-if="error" class="photo-studio__error" role="alert">{{ error }}</p>
    <p v-if="notice" class="photo-studio__notice" role="status">{{ notice }}</p>

    <Card v-if="canStart" variant="outline">
      <div class="photo-studio__block">
        <div class="photo-studio__heading">
          <strong>1. Choose the event</strong>
          <small>Defaults to the event most recently sent to print, across committee devices.</small>
        </div>
        <label class="photo-studio__event-select">Event
          <select v-model.number="selectedEventId" :disabled="busy || eventsLoading">
            <option v-if="!events.length" :value="null">{{ eventsLoading ? 'Loading events…' : 'No available events' }}</option>
            <option v-for="event in events" :key="event.id" :value="event.id">
              {{ event.title }} · {{ new Date(event.startsAt).toLocaleDateString('en-GB', { day:'numeric', month:'short', year:'numeric' }) }}
            </option>
          </select>
        </label>
        <div v-if="selectedEvent" class="photo-studio__event-summary">
          <span>Heading on print: <strong>{{ selectedEvent.photoShortName || selectedEvent.title }}</strong></span>
          <span v-if="selectedEvent.photoShortName" class="muted">Full event name stays on the digital photo page.</span>
        </div>
        <div v-if="selectedEvent?.canEditShortName" class="photo-studio__short-name">
          <label>Optional photo short name
            <input v-model="shortName" type="text" maxlength="64"
                   placeholder="e.g. Halloween Social" :disabled="busy || savingShortName">
          </label>
          <Button size="sm" variant="secondary"
                  :disabled="busy || savingShortName || shortName.trim() === (selectedEvent.photoShortName || '')"
                  @click="saveShortName">Save photo short name</Button>
        </div>
        <div class="photo-studio__heading">
          <strong>2. Select 1–4 photos</strong><small>The layout follows the number of photos. JPEG from Imaging Edge Mobile is ideal; photos from your iPhone work too.</small></div>
        <label class="photo-studio__picker">
          <span>+ Add photos ({{ selections.length }}/4)</span>
          <input type="file" multiple accept="image/*" :disabled="busy || selections.length >= 4" @change="pick">
        </label>
        <div v-if="selections.length" class="photo-studio__choices">
          <div v-for="(entry, index) in selections" :key="entry.url" class="photo-studio__choice">
            <img :src="entry.url" :alt="`Photo ${index+1}`">
            <div class="photo-studio__choice-controls">
              <button type="button" :disabled="index === 0 || busy" aria-label="Move photo earlier" @click="movePhoto(index, -1)">←</button>
              <span>{{ index+1 }}</span>
              <button type="button" :disabled="index === selections.length-1 || busy" aria-label="Move photo later" @click="movePhoto(index, 1)">→</button>
              <button type="button" :disabled="busy" :aria-label="`Remove photo ${index+1}`" @click="removePhoto(index)">×</button>
            </div>
          </div>
        </div>
        <div v-if="selections.length && selectedEvent" class="photo-studio__crop-section">
          <strong>3. Crop and resize your print</strong>
          <BoothPrintEditor :photos="selections"
                            :event-name="selectedEvent.photoShortName || selectedEvent.title"
                            :event-date="eventDate(selectedEvent.startsAt)" :event-venue="selectedEvent.venue || ''"
                            qr-url="https://eesoc.com/api/p/preview" :disabled="busy"
                            @change="updateSelectionFraming" />
        </div>
        <p class="muted photo-studio__hint">The layout is the supplied 100 × 148 mm portrait template for the SELPHY CP1500. The digital gallery preserves each original full-resolution photo. The print QR becomes active when published.</p>
        <Button :disabled="busy || !selections.length || !selectedEvent" @click="createDraft">{{ busy ? (progress || 'Working…') : '4. Create photo draft' }}</Button>
      </div>
    </Card>
    <p v-else class="muted">Your committee permissions do not include creating photo prints. Ask an administrator to grant <code>photos.view</code>, <code>photos.upload</code> and <code>photos.create</code>.</p>

    <Card v-if="active" variant="outline">
      <div class="photo-studio__block">
        <div class="photo-studio__heading">
          <strong>Review &amp; print</strong>
          <small>{{ active.printLabel }} · {{ active.count }} photo{{ active.count === 1 ? '' : 's' }} · {{ active.status }}</small>
        </div>
        <div v-if="active.status === 'draft' && can('photos.framing.edit') && activePhotos.length" class="photo-studio__crop-section">
          <strong>Adjust this draft's crops</strong>
          <BoothPrintEditor :photos="activePhotos" :event-name="active.printLabel"
                            :event-date="active.eventDate" :event-venue="active.eventVenue"
                            :qr-url="active.previewQrUrl || ''" :disabled="busy"
                            @change="updateDraftFraming" />
          <Button :disabled="busy || !framingChanged" variant="secondary" @click="saveFraming">Save crop and zoom</Button>
          <small class="muted">Save your changes before publishing. Published prints cannot be silently edited.</small>
        </div>
        <img v-if="previewUrl" :src="previewUrl" alt="Print-ready EESoc collage with QR code" class="photo-studio__print-preview">
        <p v-else class="muted">Preparing print preview…</p>
        <div class="photo-studio__actions">
          <Button v-if="active.status === 'draft' && !framingChanged && can('photos.publish')" :disabled="busy" @click="mutate(active, 'publish')">Publish &amp; enable QR</Button>
          <Button v-if="active.status === 'published' && previewUrl && can('photos.print')" :disabled="busy" @click="sharePrint">Share / AirPrint</Button>
          <Button v-if="active.status === 'published'" variant="secondary" @click="shareLink">Copy QR link</Button>
          <a v-if="active.status === 'published' && previewUrl && can('photos.print')" class="photo-studio__link" :href="printUrl" target="_blank" rel="noopener" @click="openFullSizePrint">Open full-size print</a>
          <a v-if="active.url" class="photo-studio__link" :href="active.url" target="_blank" rel="noopener">Open guest gallery</a>
          <Button v-if="active.status === 'published' && can('photos.unpublish')" variant="ghost" :disabled="busy" @click="mutate(active, 'unpublish')">Unpublish</Button>
          <Button v-if="can('photos.delete')" variant="danger" :disabled="busy" @click="mutate(active, 'delete')">Delete set</Button>
        </div>
        <p class="muted photo-studio__hint">Every print uses the supplied portrait-only EESoc template. On iPhone, tap <b>Share / AirPrint</b> then <b>Print</b>. Choose your SELPHY CP1500, 4×6 paper and borderless printing. Confirm the QR scans before giving the print to a guest.</p>
        <p v-if="active.status === 'published'" class="photo-studio__privacy">Anyone with the printed QR or this link can view and download this set. Only publish photos you're allowed to share.</p>
      </div>
    </Card>

    <section v-if="can('photos.view')" class="photo-studio__history">
      <h3>Recent photo sets</h3>
      <p v-if="!sets.length" class="muted">No photo sets yet. Create one above to get started.</p>
      <div v-else class="photo-studio__set-list">
        <button v-for="set in sets" :key="set.id" class="photo-studio__set" type="button" @click="openSet(set)">
          <img v-if="set.images[0]" :src="set.images[0].url" alt="">
          <span><strong>{{ set.title }}</strong><small>{{ set.count }} photos · {{ set.status }} · {{ new Date(set.createdAt).toLocaleDateString('en-GB') }}</small></span>
          <span aria-hidden="true">→</span>
        </button>
      </div>
    </section>
  </section>
</template>

<style scoped>
.photo-studio { display: grid; gap: var(--space-6); min-width: 0; }
.photo-studio__header h2 { font: var(--type-h3); margin: var(--space-2) 0; }
.photo-studio__header p { max-width: 58ch; }
.photo-studio__block { display: grid; gap: var(--space-4); }
.photo-studio__heading { display: grid; gap: var(--space-1); }
.photo-studio__heading strong { font: var(--type-h4); }
.photo-studio__heading small, .photo-studio__set small { color: var(--text-muted); }
.photo-studio__picker { position: relative; display: flex; padding: var(--space-5); border: 2px dashed var(--border-default); border-radius: var(--radius-md); font-weight: 700; cursor: pointer; justify-content: center; }
.photo-studio__picker:has(input:disabled) { opacity: .5; cursor: not-allowed; }
.photo-studio__picker input { position: absolute; inset: 0; opacity: 0; width: 100%; cursor: pointer; }
.photo-studio__choices { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-3); }
.photo-studio__choice { min-width: 0; border: 1px solid var(--border-default); border-radius: var(--radius-sm); overflow: hidden; }
.photo-studio__choice img { display: block; width: 100%; height: 140px; object-fit: cover; }
.photo-studio__choice-controls { display: flex; align-items: center; justify-content: space-around; padding: 4px; }
.photo-studio__choice-controls button { border: 0; background: transparent; padding: 8px; cursor: pointer; font-size: 18px; color: var(--text-body); }
.photo-studio__choice-controls button:disabled { opacity: .25; }
 .photo-studio__event-select, .photo-studio__short-name label {
  display: grid; gap: var(--space-2); font-size: var(--text-sm); font-weight: 700;
}
.photo-studio__event-select select, .photo-studio__short-name input {
  width: 100%; min-width: 0; color: var(--text-body); font-size: 16px;
  background: var(--surface-page); border: 1px solid var(--border-default);
  padding: 12px; border-radius: var(--radius-sm);
}
.photo-studio__event-summary {
  display: grid; gap: 5px; border-left: 3px solid var(--gold-400);
  padding: var(--space-2) var(--space-3); background: var(--surface-accent-subtle);
  font-size: var(--text-sm);
}
.photo-studio__short-name { display: grid; justify-items: start; gap: var(--space-2); }
.photo-studio__short-name label { width: 100%; }
.photo-studio__crop-section { display:grid; justify-items:stretch; gap:var(--space-3); }
.photo-studio__controls { display: grid; grid-template-columns: repeat(auto-fit,minmax(min(100%,220px),1fr)); gap: var(--space-3); }
.photo-studio__controls label { display: grid; gap: var(--space-2); font-size: var(--text-sm); font-weight: 700; }
.photo-studio__controls input, .photo-studio__controls select { padding: 12px; min-width: 0; width: 100%; background: var(--surface-page); border: 1px solid var(--border-default); border-radius: var(--radius-sm); color: var(--text-body); font-size: 16px; }
.photo-studio__hint { font-size: var(--text-xs); margin: 0; }
.photo-studio__print-preview { width: min(100%,410px); height: auto; object-fit: contain; margin: 0 auto; display: block; border: 1px solid var(--border-default); }
.photo-studio__actions { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-3); }
.photo-studio__link { padding: var(--space-2); }
.photo-studio__privacy { font-size: var(--text-xs); margin: 0; }
.photo-studio__history h3 { font: var(--type-h4); }
.photo-studio__set-list { display: grid; gap: var(--space-2); }
.photo-studio__set { display: flex; align-items: center; gap: var(--space-3); padding: var(--space-3); min-width: 0; width: 100%; text-align: left; border: 1px solid var(--border-default); background: var(--surface-page); color: var(--text-body); border-radius: var(--radius-sm); cursor: pointer; }
.photo-studio__set:hover { border-color: var(--text-brand); }
.photo-studio__set img { width: 64px; height: 55px; object-fit: cover; border-radius: var(--radius-sm); }
.photo-studio__set span:nth-child(2) { min-width: 0; flex: 1; display: grid; gap: 3px; }
.photo-studio__set strong { overflow-wrap: anywhere; }
.photo-studio__error { color: var(--danger); }
.photo-studio__notice { font-weight: 600; }
@media(max-width: 520px) {
  .photo-studio__actions :deep(.ees-btn) { flex: 1 1 auto; }
  .photo-studio__choice img { height: 112px; }
}
</style>
