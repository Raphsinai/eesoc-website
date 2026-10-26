<script setup>
// Mobile photo booth: Sony -> iPhone -> EESoc -> AirPrint/SELPHY.
// One photo per request avoids the nginx 12 MB body cap with four Sony originals.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import api from '@/api'
import { can } from '@/stores/session'
import Button from '@/components/ds/Button.vue'
import Card from '@/components/ds/Card.vue'

const selections = ref([])
const title = ref('EESoc memories')
const orientation = ref('portrait')
const busy = ref(false)
const error = ref('')
const notice = ref('')
const progress = ref('')
const sets = ref([])
const active = ref(null)
const previewUrl = ref('')
const printFile = ref(null)

const canStart = computed(() => can('photos.view') && can('photos.upload') && can('photos.create'))
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
    selections.value.push({ file, url: URL.createObjectURL(file) })
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
onMounted(loadSets)

async function openSet(set) {
  active.value = set
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
    printFile.value = new File([blob], 'eesoc-photo-print.png', { type: 'image/png' })
    previewUrl.value = URL.createObjectURL(blob)
  } catch (e) {
    error.value = e.message
  }
}

async function createDraft() {
  if (!canStart.value || busy.value || !selections.value.length) return
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
      title: title.value.trim() || 'EESoc memories',
      orientation: orientation.value,
      image_ids: ids,
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
async function sharePrint() {
  if (!printFile.value) return
  if (navigator.canShare?.({ files: [printFile.value] }) && navigator.share) {
    // This call happens directly in the click handler: Safari requires a user
    // gesture to expose the native Share / AirPrint sheet.
    try {
      await navigator.share({ files: [printFile.value] })
    } catch (e) {
      if (e.name !== 'AbortError') error.value = e.message
    }
  } else {
    window.open(printUrl.value, '_blank', 'noopener')
  }
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
        <div class="photo-studio__heading"><strong>1. Select 1–4 photos</strong><small>JPEG from Imaging Edge Mobile is ideal; photos from your iPhone work too.</small></div>
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
        <div class="photo-studio__controls">
          <label>Print title
            <input v-model="title" maxlength="100" type="text" placeholder="EESoc memories">
          </label>
          <label>Print orientation
            <select v-model="orientation">
              <option value="portrait">Portrait · 4×6</option>
              <option value="landscape">Landscape · 6×4</option>
            </select>
          </label>
        </div>
        <p class="muted photo-studio__hint">The digital photo set keeps all 1–4 photos separately, at high resolution. The print is a branded collage; photos are centre-cropped to fit. The QR link becomes available after publishing.</p>
        <Button :disabled="busy || !selections.length" @click="createDraft">{{ busy ? (progress || 'Working…') : '2. Create photo print draft' }}</Button>
      </div>
    </Card>
    <p v-else class="muted">Your committee permissions do not include creating photo prints. Ask an administrator to grant <code>photos.view</code>, <code>photos.upload</code> and <code>photos.create</code>.</p>

    <Card v-if="active" variant="outline">
      <div class="photo-studio__block">
        <div class="photo-studio__heading">
          <strong>2. Review &amp; print</strong>
          <small>{{ active.title }} · {{ active.count }} photo{{ active.count === 1 ? '' : 's' }} · {{ active.status }}</small>
        </div>
        <img v-if="previewUrl" :src="previewUrl" alt="EESoc collage preview with QR code" class="photo-studio__print-preview">
        <p v-else class="muted">Preparing print preview…</p>
        <div class="photo-studio__actions">
          <Button v-if="active.status === 'draft' && can('photos.publish')" :disabled="busy" @click="mutate(active, 'publish')">3. Publish &amp; enable QR</Button>
          <Button v-if="active.status === 'published' && previewUrl" :disabled="busy" @click="sharePrint">Share / AirPrint</Button>
          <Button v-if="active.status === 'published'" variant="secondary" @click="shareLink">Copy QR link</Button>
          <a v-if="active.status === 'published' && previewUrl" class="photo-studio__link" :href="printUrl" target="_blank" rel="noopener">Open full-size print</a>
          <a v-if="active.url" class="photo-studio__link" :href="active.url" target="_blank" rel="noopener">Open guest gallery</a>
          <Button v-if="active.status === 'published' && can('photos.unpublish')" variant="ghost" :disabled="busy" @click="mutate(active, 'unpublish')">Unpublish</Button>
          <Button v-if="can('photos.delete')" variant="danger" :disabled="busy" @click="mutate(active, 'delete')">Delete set</Button>
        </div>
        <p class="muted photo-studio__hint">On iPhone, tap <b>Share / AirPrint</b> then <b>Print</b>. Choose your SELPHY CP1500, 4×6 paper and borderless printing. Confirm the QR scans before giving the print to a guest.</p>
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
