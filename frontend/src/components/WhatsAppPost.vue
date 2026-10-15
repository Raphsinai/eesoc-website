<script setup>
// The WhatsApp version of an announcement, and the ways to post it.
//
// The post is the same words in WhatsApp's formatting, with an event's
// details added from the database. "Open in WhatsApp" hands it to the app
// with the text filled in, for somebody to send by hand. When the society's
// number is linked (WhatsAppLink) and groups are chosen, "Post to …" sends it
// into those groups from here — and in the email queue, a tick sends it along
// with the email at its time.
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
  // In the email queue only: whether it also goes to the chosen groups when
  // the email is sent. Null leaves the tick off the screen.
  alsoWhenSent: { type: Boolean, default: null },
  // The email is approved and read-only.
  locked: { type: Boolean, default: false },
  // What went to WhatsApp with the email: [{group, status, error}].
  posted: { type: Array, default: () => [] },
})
const emit = defineEmits(['update:alsoWhenSent'])

const shown = ref(false)
const post = ref(null) // { text, url, warning }
const stale = ref(false)
const loading = ref(false)
const error = ref('')
const copied = ref(false)
const posting = ref(false)
const notice = ref('')

const targetNames = computed(() => targets.value.map((g) => g.name || g.jid).join(', '))

const empty = computed(() => !props.eventId && !props.heading.trim() && !props.body.trim())
// Not while the text on screen is behind the words: posting the older one to
// a group of hundreds is not something that can be taken back quietly.
const ready = computed(() => Boolean(post.value?.url) && !stale.value && !loading.value)

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

if (props.alsoWhenSent !== null && !whatsapp.loaded) loadWhatsApp()

/** Post it into the chosen groups, now. The server renders it again from the
 *  same words, so what is on screen is what goes. */
async function postNow() {
  if (!confirm(`Post this into ${targetNames.value} now, from the society's number? It can't be taken back quietly.`)) return
  posting.value = true
  error.value = ''
  notice.value = ''
  try {
    const res = await api.post('/admin/whatsapp/post', {
      heading: props.heading,
      body: props.body,
      event_id: props.eventId,
      kind: props.kind,
    })
    notice.value = res.message
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
    <!-- Only where WhatsApp posting is switched on at all: elsewhere a tick
         that can never be ticked is just noise. -->
    <label v-if="alsoWhenSent !== null && whatsapp.data?.configured" class="wa__also">
      <input
        type="checkbox"
        :checked="alsoWhenSent"
        :disabled="locked || (!alsoWhenSent && !targets.length)"
        @change="emit('update:alsoWhenSent', !alsoWhenSent)"
      />
      <span>
        <strong>Also post it on WhatsApp when it goes</strong>
        <span class="wa__help">
          <template v-if="targets.length">Into {{ targetNames }}, at the same moment as the email.</template>
          <template v-else-if="alsoWhenSent">No WhatsApp group would get it right now — link the number and choose groups on the Email tab, or it goes by email only.</template>
          <template v-else>Link the society's number and choose groups on the Email tab first.</template>
        </span>
      </span>
    </label>
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
          :disabled="!ready || posting"
          @click="postNow"
        >
          {{ posting ? 'Posting…' : `Post to ${targets.length === 1 ? targetNames : `${targets.length} groups`}` }}
        </Button>
        <span v-if="stale || loading" class="wa__help">Updating…</span>
      </div>
      <p v-if="notice" class="wa__notice">{{ notice }}</p>
      <p class="wa__help">
        Open in WhatsApp fills it in for you to choose a chat and send — nothing
        is posted until you do. For a channel, copy it and paste it there.<template v-if="targets.length">
        Post to sends it from the society's number into {{ targetNames }} straight away.</template>
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
.wa__posted {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: var(--space-3);
  align-items: start;
  font: var(--type-small);
}

.wa__also input {
  margin-top: 3px;
  width: 15px;
  height: 15px;
  accent-color: var(--action-primary);
}

.wa__also .wa__help {
  display: block;
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
