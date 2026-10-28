<script setup>
// Interactive view of the supplied SVG template. No screenshot printing: the
// server re-renders with these exact stored placement coordinates at 300 DPI.
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import qrGenerator from 'qrcode-generator'

const props = defineProps({
  photos: { type: Array, required: true },
  eventName: { type: String, default: '' },
  eventDate: { type: String, default: '' },
  eventVenue: { type: String, default: '' },
  qrUrl: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['change'])
const stage = ref(null)
const markup = ref('')
const selected = ref(0)
const error = ref('')
const busy = ref(false)
const templates = new Map()
const sizes = new Map()
const pointers = new Map()
const page = { width: 378, height: 559 }
const windows = {
  1: [{ x:18, y:73, w:342, h:386 }],
  2: [{ x:18, y:73, w:342, h:187 }, { x:18, y:272, w:342, h:187 }],
  3: [{ x:18, y:73, w:342, h:218.167 }, { x:18, y:303.167, w:165, h:155.833 }, { x:195, y:303.167, w:165, h:155.833 }],
  4: [{ x:18, y:73, w:165, h:187 }, { x:195, y:73, w:165, h:187 }, { x:18, y:272, w:165, h:187 }, { x:195, y:272, w:165, h:187 }],
}
const count = computed(() => Math.max(1, Math.min(4, props.photos.length)))
const slot = computed(() => windows[count.value][selected.value] || windows[count.value][0])
const position = computed(() => props.photos[selected.value] || {})
const currentZoom = computed(() => position.value.zoom ?? 1)
const currentX = computed(() => position.value.x ?? .5)
const currentY = computed(() => position.value.y ?? .5)
const bound = (x, low, high) => Math.max(low, Math.min(high, x))
const svgNS = 'http://www.w3.org/2000/svg'

function cleanImageUrl(value) {
  try {
    const url = new URL(value, location.href)
    return ['https:', 'http:', 'blob:'].includes(url.protocol) ? url.href : null
  } catch { return null }
}
function getSize(src) {
  if (!sizes.has(src)) {
    sizes.set(src, new Promise((resolve, reject) => {
      const image = new Image()
      image.onload = () => resolve({ width: image.naturalWidth, height: image.naturalHeight })
      image.onerror = () => reject(new Error('Cannot display this photo. Convert HEIC to JPEG first.'))
      image.src = src
    }))
  }
  return sizes.get(src)
}
function geometry(rect, {width: iw, height: ih}, p = {}) {
  const zoom = bound(Number(p.zoom ?? 1), 1, 8)
  const scale = Math.max(rect.w/iw, rect.h/ih)*zoom
  const dw = iw*scale, dh = ih*scale
  return { scale, dw, dh,
    left: rect.x+(rect.w-dw)*Number(p.x ?? .5),
    top: rect.y+(rect.h-dh)*Number(p.y ?? .5),
  }
}
function createQr(doc, group, value) {
  if (!value) return
  const qr = qrGenerator(0, 'M')
  qr.addData(value)
  qr.make()
  const n = qr.getModuleCount()
  const size = 44/n
  for (let y=0; y<n; y++) for(let x=0; x<n; x++) if(qr.isDark(y,x)) {
    const cell = doc.createElementNS(svgNS, 'rect')
    cell.setAttribute('x', String(312+x*size))
    cell.setAttribute('y', String(481+y*size))
    cell.setAttribute('width', String(size+.002))
    cell.setAttribute('height', String(size+.002))
    cell.setAttribute('fill', '#0B0D12')
    group.appendChild(cell)
  }
}
async function fitText() {
  await nextTick()
  const root = stage.value?.querySelector('svg')
  if (!root) return
  for (const [id, initial, minimum] of [['event-name',14,10],['event-details',9,7]]) {
    const el = root.querySelector(`[id="${id}"]`)
    if (!el) continue
    let size = initial
    while (size > minimum && el.getComputedTextLength() > 276) {
      size -= .5
      el.setAttribute('font-size', String(size))
    }
    if (el.getComputedTextLength() <= 276) continue
    const segmenter = new Intl.Segmenter('en', {granularity:'grapheme'})
    const tokens = [...segmenter.segment(el.textContent)].map(x => x.segment)
    while (tokens.length && el.getComputedTextLength() > 276) {
      tokens.pop()
      el.textContent = tokens.join('').trimEnd() + '…'
    }
  }
}
let renderGeneration = 0
async function render() {
  const generation = ++renderGeneration
  if (!props.photos.length) { markup.value = ''; return }
  busy.value = true
  error.value = ''
  try {
    if (!templates.has(count.value)) {
      const result = await fetch(`/booth/booth-${count.value}-photo.svg`)
      if (!result.ok) throw new Error('Could not load photobooth template')
      templates.set(count.value, await result.text())
    }
    const doc = new DOMParser().parseFromString(templates.get(count.value), 'image/svg+xml')
    if (doc.querySelector('parsererror')) throw new Error('Invalid photobooth SVG')
    const name = doc.getElementById('event-name')
    const details = doc.getElementById('event-details')
    name.textContent = props.eventName || 'EESoc'
    details.textContent = [props.eventDate, props.eventVenue].filter(Boolean).join(' · ').toUpperCase()
    const sizeList = await Promise.all(props.photos.map(async (photo) => {
      const safe = cleanImageUrl(photo.url)
      if (!safe) throw new Error('Invalid photo URL')
      return { url: safe, size: await getSize(safe) }
    }))
    if (generation !== renderGeneration) return
    sizeList.forEach(({url,size}, i) => {
      const dest = windows[count.value][i]
      const area = geometry(dest, size, props.photos[i])
      const node = doc.createElementNS(svgNS, 'image')
      node.setAttribute('href', url)
      node.setAttribute('x', String(area.left))
      node.setAttribute('y', String(area.top))
      node.setAttribute('width', String(area.dw))
      node.setAttribute('height', String(area.dh))
      node.setAttribute('preserveAspectRatio', 'none')
      doc.getElementById(`photo-${i+1}`).appendChild(node)
    })
    createQr(doc, doc.getElementById('qr'), props.qrUrl)
    markup.value = new XMLSerializer().serializeToString(doc.documentElement)
    await fitText()
  } catch(e) { if (generation === renderGeneration) error.value = e.message }
  finally { if (generation === renderGeneration) busy.value = false }
}
watch(() => [props.photos.map(p => `${p.url}:${p.x}:${p.y}:${p.zoom}`).join('|'), props.eventName, props.eventDate, props.eventVenue, props.qrUrl], render)
watch(count, () => { selected.value = 0 })
onMounted(render)

function changeAt(index, next) {
  if (props.disabled || index < 0 || index >= props.photos.length) return
  emit('change', props.photos.map((p,i) => i === index
    ? { x: bound(Number(next.x ?? p.x ?? .5),0,1), y: bound(Number(next.y ?? p.y ?? .5),0,1),
        zoom: bound(Number(next.zoom ?? p.zoom ?? 1),1,8) }
    : { x:p.x ?? .5, y:p.y ?? .5, zoom:p.zoom ?? 1 }))
}
function stagePoint(e) {
  const bounds = stage.value.getBoundingClientRect()
  return {x:(e.clientX-bounds.left)/bounds.width*page.width,
          y:(e.clientY-bounds.top)/bounds.height*page.height}
}
function hit(point) {return windows[count.value].findIndex(w => point.x >= w.x && point.x <= w.x+w.w && point.y >= w.y && point.y <= w.y+w.h)}
function pan(dx,dy) {
  const p=props.photos[selected.value]
  const size=sizes.get(cleanImageUrl(p?.url))
  if (!size) return
  size.then(dim => {
    const g=geometry(slot.value,dim,p)
    changeAt(selected.value, {x:Math.abs(g.dw-slot.value.w)<1e-8 ? .5 : (p.x??.5)-dx/(g.dw-slot.value.w),
                              y:Math.abs(g.dh-slot.value.h)<1e-8 ? .5 : (p.y??.5)-dy/(g.dh-slot.value.h)})
  })
}
async function zoom(factor,anchor) {
  const index=selected.value
  const p=props.photos[index]
  if (!p) return
  const size=await getSize(cleanImageUrl(p.url))
  const w=windows[count.value][index], before=geometry(w,size,p)
  const u=(anchor.x-before.left)/before.dw, v=(anchor.y-before.top)/before.dh
  const newZoom=bound((p.zoom??1)*factor,1,8)
  const after=geometry(w,size,{...p,zoom:newZoom})
  const left=anchor.x-u*after.dw, top=anchor.y-v*after.dh
  changeAt(index,{zoom:newZoom,
    x:Math.abs(after.dw-w.w)<1e-8?.5:(w.x-left)/(after.dw-w.w),
    y:Math.abs(after.dh-w.h)<1e-8?.5:(w.y-top)/(after.dh-w.h)})
}
function reset() {changeAt(selected.value,{x:.5,y:.5,zoom:1})}
function pointerDown(e) {
  if (props.disabled) return
  const point=stagePoint(e), index=hit(point)
  if (index < 0) return
  selected.value=index
  e.currentTarget.setPointerCapture(e.pointerId)
  pointers.set(e.pointerId, point)
}
function pointerMove(e) {
  if (!pointers.has(e.pointerId)||props.disabled) return
  const previous=pointers.get(e.pointerId), now=stagePoint(e)
  pointers.set(e.pointerId,now)
  if (pointers.size===1) pan(now.x-previous.x,now.y-previous.y)
  else if(pointers.size===2) {
    const list=[...pointers.values()]
    const old= e.pointerId === [...pointers.keys()][0] ? [previous,list[1]] : [list[0],previous]
    const prevDist=Math.hypot(old[0].x-old[1].x,old[0].y-old[1].y)
    const dist=Math.hypot(list[0].x-list[1].x,list[0].y-list[1].y)
    if(prevDist>0) zoom(dist/prevDist,{x:(list[0].x+list[1].x)/2,y:(list[0].y+list[1].y)/2})
  }
}
function pointerUp(e) { pointers.delete(e.pointerId) }
function wheel(e) {
  if (props.disabled) return
  const p=stagePoint(e), i=hit(p)
  if (i<0) return
  e.preventDefault()
  selected.value=i
  zoom(Math.exp(-e.deltaY*.0015),p)
}
function doubleClick(e) {
  if(props.disabled) return
  const i=hit(stagePoint(e)); if(i<0)return
  selected.value=i;reset()
}
function sliderZoom(e) {zoom(Number(e.target.value)/currentZoom.value,{x:slot.value.x+slot.value.w/2,y:slot.value.y+slot.value.h/2})}
</script>

<template>
  <div class="booth-editor">
    <div class="booth-editor__stage" ref="stage" role="group" aria-label="Photobooth collage preview"
         @pointerdown="pointerDown" @pointermove="pointerMove"
         @pointerup="pointerUp" @pointercancel="pointerUp" @lostpointercapture="pointerUp"
         @wheel="wheel" @dblclick="doubleClick">
      <div class="booth-editor__svg" v-html="markup"></div>
      <div v-if="markup && !disabled" v-for="(rect,i) in windows[count]" :key="i" class="booth-editor__outline"
           :class="{'booth-editor__outline--selected':selected===i}"
           :style="{left:`${rect.x/378*100}%`,top:`${rect.y/559*100}%`,width:`${rect.w/378*100}%`,height:`${rect.h/559*100}%`}" aria-hidden="true" />
    </div>
    <p v-if="error" role="alert" class="booth-editor__error">{{ error }}</p>
    <p v-else class="booth-editor__hint">Drag to crop · pinch or scroll to resize · double-click to reset. The finished print uses the original high-resolution photos.</p>
    <div v-if="!disabled && photos.length" class="booth-editor__controls">
      <div class="booth-editor__tabs" role="group" aria-label="Choose a photo to crop">
        <button v-for="(photo,i) in photos" :key="photo.url + i" type="button"
                :aria-pressed="selected===i" @click="selected=i">Photo {{ i+1 }}</button>
      </div>
      <label>Zoom <input type="range" :value="currentZoom" min="1" max="8" step="0.01" @input="sliderZoom" /> <output>{{ currentZoom.toFixed(2) }}×</output></label>
      <label>Horizontal crop <input type="range" :value="currentX" min="0" max="1" step="0.001" @input="changeAt(selected,{x:Number($event.target.value)})" /></label>
      <label>Vertical crop <input type="range" :value="currentY" min="0" max="1" step="0.001" @input="changeAt(selected,{y:Number($event.target.value)})" /></label>
      <button type="button" class="booth-editor__reset" @click="reset">Reset selected photo</button>
    </div>
  </div>
</template>

<style scoped>
.booth-editor { display:grid; gap:12px; justify-items:center; min-width:0; }
.booth-editor__stage { width:min(100%,420px); position:relative; aspect-ratio:378 / 559; background:#0B0D12; touch-action:none; cursor:grab; box-shadow:0 0 0 1px #e7b570; user-select:none; }
.booth-editor__stage:active { cursor:grabbing; }
.booth-editor__svg { width:100%; height:100%; pointer-events:none; }
.booth-editor__svg :deep(svg) { display:block; width:100%; height:100%; }
.booth-editor__outline { position:absolute; pointer-events:none; border:1px solid transparent; }
.booth-editor__outline--selected { border:2px solid #e7b570; box-shadow:inset 0 0 0 1px #0b0d12; }
.booth-editor__controls { display:grid; gap:10px; width:min(100%,420px); }
.booth-editor__tabs { display:flex; gap:5px; }
.booth-editor__tabs button { flex:1; border:1px solid var(--border-default); color:var(--text-body); background:var(--surface-page); padding:7px; border-radius:4px; cursor:pointer; }
.booth-editor__tabs button[aria-pressed=true] { border-color:var(--gold-400); background:var(--surface-accent-subtle); font-weight:700; }
.booth-editor__controls label { display:flex; align-items:center; flex-wrap:wrap; gap:9px; font-size:var(--text-sm); }
.booth-editor__controls input { accent-color:#e7b570; flex:1; min-width:120px; }
.booth-editor__controls output { width:43px; }
.booth-editor__reset { width:100%; padding:9px; border:1px solid var(--border-default); background:var(--surface-page); color:var(--text-body); border-radius:4px; cursor:pointer; }
.booth-editor__hint { margin:0; font-size:var(--text-xs); color:var(--text-muted); text-align:center; }
.booth-editor__error { color:var(--danger); }
</style>
