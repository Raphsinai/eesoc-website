<script setup>
// The WhatsApp version of an announcement, and the ways to post it.
//
// The post is the same words in WhatsApp's formatting, with an event's
// details added from the database. "Open in WhatsApp" hands it to the app
// with the text filled in, for somebody to send by hand. When the society's
// number is linked (WhatsAppLink), the groups enabled there can be picked for
// this post and posted into from here — and in the email queue, picked to go
// along with the email at its time. Nothing is picked until somebody picks it.
//
// Closed until somebody asks for it, then it follows the words as they are
// edited, so what is opened is never an older draft than what is on screen.
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import api from '@/api'
import Badge from '@/components/ds/Badge.vue'
import Button from '@/components/ds/Button.vue'
import { loadWhatsApp, problem, targets, whatsapp } from '@/stores/whatsapp'

const props = defineProps({
  heading: { type: String, default: '' },
  body: { type: String, default: '' },
  eventId: { type: Number, default: null },
  // A campaign kind. "on_sale" gives the standard opening and the shop link.
  kind: { type: String, default: null },
  // In the email queue only: the groups it also goes into when the email is
  // sent. Null leaves that choice off the screen.
  alsoGroups: { type: Array, default: null },
  // The email is approved and read-only.
  locked: { type: Boolean, default: false },
  // What went to WhatsApp with the email: [{group, status, error}].
  posted: { type: Array, default: () => [] },
})
const emit = defineEmits(['update:alsoGroups'])

const shown = ref(false)
const post = ref(null) // { text, url, warning }
const stale = ref(false)
const loading = ref(false)
const error = ref('')
const copied = ref(false)
const posting = ref(false)
const notice = ref('')
// The groups picked for posting this now. Empty until somebody ticks one.
const picked = ref([])

const labels = computed(() => Object.fromEntries(targets.value.map((g) => [g.jid, g.label])))
const names = (jids) => jids.map((j) => labels.value[j] || j).join(', ')

const empty = computed(() => !props.eventId && !props.heading.trim() && !props.body.trim())
// Not while the text on screen is behind the words: posting the older one to
// a group of hundreds is not something that can be taken back quietly.
const ready = computed(() => Boolean(post.value?.url) && !stale.value && !loading.value)

// A group picked for a queued email but disabled on the card since: it will
// not get the post, and the screen says so rather than quietly dropping it.
const missing = computed(() => (props.alsoGroups ?? []).filter((j) => !labels.value[j]))

let timer = null
let asked = 0

async function render() {
  const mine = ++asked
  loading.value = true
  error.value = ''
  try {
    const res = await api.post('/admin/whatsapp/render', {
      heading: props.heading,
      body: props.body,
      event_id: props.eventId,
      kind: props.kind,
    })
    // A later edit has already asked again; its answer is the one to show.
    if (mine !== asked) return
    post.value = res
    stale.value = false
  } catch (e) {
    if (mine === asked) error.value = problem(e)
  } finally {
    if (mine === asked) loading.value = false
  }
}

function open() {
  shown.value = true
  render()
  if (!whatsapp.loaded) loadWhatsApp()
}

if (props.alsoGroups !== null && !whatsapp.loaded) loadWhatsApp()

function togglePicked(jid) {
  picked.value = picked.value.includes(jid)
    ? picked.value.filter((j) => j !== jid)
    : [...picked.value, jid]
}

function toggleAlso(jid) {
  const now = props.alsoGroups ?? []
  emit('update:alsoGroups', now.includes(jid) ? now.filter((j) => j !== jid) : [...now, jid])
}

/** Post it into the picked groups, now. The server renders it again from the
 *  same words, so what is on screen is what goes. */
async function postNow() {
  if (!confirm(`Post this into ${names(picked.value)} now, from the society's number? It can't be taken back quietly.`)) return
  posting.value = true
  error.value = ''
  notice.value = ''
  try {
    const res = await api.post('/admin/whatsapp/post', {
      heading: props.heading,
      body: props.body,
      event_id: props.eventId,
      kind: props.kind,
      groups: picked.value,
    })
    notice.value = res.message
    picked.value = []
  } catch (e) {
    error.value = problem(e)
  } finally {
    posting.value = false
    loadWhatsApp()
  }
}

watch(
  () => [props.heading, props.body, props.eventId, props.kind],
  () => {
    if (!shown.value) return
    stale.value = true
    clearTimeout(timer)
    timer = setTimeout(render, 400)
  },
)

onBeforeUnmount(() => clearTimeout(timer))

async function copy() {
  try {
    await navigator.clipboard.writeText(post.value.text)
    copied.value = true
    setTimeout(() => (copied.value = false), 2000)
  } catch {
    error.value = "Couldn't copy — select the text above and copy it by hand."
  }
}
</script>

<template>
  <div class="wa">
    <!-- In the queue: which groups also get it when the email goes. Only
         where WhatsApp posting is switched on at all. -->
    <div v-if="alsoGroups !== null && whatsapp.data?.configured" class="wa__also">
      <span class="wa__label">Also post it on WhatsApp when it goes</span>
      <label v-for="g in targets" :key="g.jid" class="wa__pick">
        <input
          type="checkbox"
          :checked="alsoGroups.includes(g.jid)"
          :disabled="locked"
          @change="toggleAlso(g.jid)"
        />
        <span>{{ g.label }}<span v-if="g.size" class="wa__size"> · {{ g.size }}</span></span>
      </label>
      <p v-if="!targets.length" class="wa__help">
        Link the society's number and enable groups on the Email tab's WhatsApp card first.
      </p>
      <p v-else class="wa__help">At the same moment as the email. None is picked unless you pick it.</p>
      <p v-if="missing.length" class="wa__warning">
        {{ missing.length }} group(s) picked for this aren't enabled any more, so won't get it.
      </p>
    </div>
    <div v-for="p in posted" :key="p.group" class="wa__posted">
      <Badge :tone="p.status === 'sent' ? 'success' : p.status === 'failed' ? 'danger' : 'accent'">{{ p.status }}</Badge>
      <span>{{ p.group }}<template v-if="p.error"> — {{ p.error }}</template></span>
    </div>

    <template v-if="!shown">
      <p class="wa__help">
        Post this on WhatsApp too: the same words in WhatsApp's formatting<template v-if="eventId">,
        with the date, venue, prices and link added from the event</template>.
      </p>
      <div>
        <Button size="sm" variant="secondary" :disabled="empty" @click="open">WhatsApp version</Button>
      </div>
    </template>

    <template v-else>
      <span class="wa__label">WhatsApp post</span>
      <p v-if="post?.warning" class="wa__warning">{{ post.warning }}</p>
      <pre v-if="post?.text" class="wa__text" :class="{ 'wa__text--stale': stale }">{{ post.text }}</pre>
      <p v-else-if="!loading && post" class="wa__help">Nothing to post yet — write something above.</p>

      <!-- Where to post it now: any of the enabled groups, none ticked. -->
      <div v-if="targets.length" class="wa__groups">
        <span class="wa__label">Post into</span>
        <label v-for="g in targets" :key="g.jid" class="wa__pick">
          <input type="checkbox" :checked="picked.includes(g.jid)" :disabled="posting" @change="togglePicked(g.jid)" />
          <span>{{ g.label }}<span v-if="g.size" class="wa__size"> · {{ g.size }}</span></span>
        </label>
      </div>

      <div class="wa__actions">
        <!-- No link while the text is out of date: an anchor still navigates
             when it only looks disabled. -->
        <Button
          size="sm"
          :href="ready ? post.url : null"
          :disabled="!ready"
          target="_blank"
          rel="noopener"
        >
          Open in WhatsApp
        </Button>
        <Button size="sm" variant="secondary" :disabled="!ready" @click="copy">
          {{ copied ? 'Copied' : 'Copy' }}
        </Button>
        <Button
          v-if="targets.length"
          size="sm"
          variant="secondary"
          :disabled="!ready || posting || !picked.length"
          @click="postNow"
        >
          {{ posting ? 'Posting…' : picked.length ? `Post to ${picked.length === 1 ? names(picked) : `${picked.length} groups`}` : 'Post to…' }}
        </Button>
        <span v-if="stale || loading" class="wa__help">Updating…</span>
      </div>
      <p v-if="notice" class="wa__notice">{{ notice }}</p>
      <p class="wa__help">
        Open in WhatsApp fills it in for you to choose a chat and send — nothing
        is posted until you do. For a channel, copy it and paste it there.<template v-if="targets.length">
        Or tick groups above and Post: it goes from the society's number straight away.</template>
      </p>
      <p v-if="error" class="wa__warning">{{ error }}</p>
    </template>
  </div>
</template>

<style scoped>
.wa {
  display: grid;
  gap: var(--space-3);
}

.wa__also,
.wa__groups {
  display: grid;
  gap: var(--space-2);
}

.wa__pick,
.wa__posted {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: var(--space-3);
  align-items: start;
  font: var(--type-small);
}

.wa__pick {
  cursor: pointer;
}

.wa__pick input {
  margin-top: 3px;
  width: 15px;
  height: 15px;
  accent-color: var(--action-primary);
}

.wa__size {
  font: var(--type-2xs);
  color: var(--text-muted);
}

.wa__notice {
  margin: 0;
  font: var(--type-small);
  color: var(--text-strong);
}

.wa__label {
  font: var(--type-label);
  color: var(--text-muted);
}

.wa__help {
  margin: 0;
  font: var(--type-2xs);
  color: var(--text-muted);
}

.wa__warning {
  margin: 0;
  font: var(--type-small);
  color: var(--danger);
}

/* Shown as the characters that will be pasted, stars and all — WhatsApp
   draws *this* as bold, and the committee should see exactly what it gets. */
.wa__text {
  margin: 0;
  max-height: 360px;
  overflow: auto;
  padding: var(--space-4);
  border: var(--border-width-hairline) solid var(--border-default);
  border-radius: var(--radius-sm);
  font-family: var(--font-sans);
  font-size: var(--text-sm);
  line-height: 1.5;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.wa__text--stale {
  opacity: 0.5;
}

.wa__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  align-items: center;
}
</style>
