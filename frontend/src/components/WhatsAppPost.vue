<script setup>
// The WhatsApp version of an announcement: what goes into a WhatsApp group
// when one is ticked under "Who gets it", beside the email audiences.
//
// The post is the same words in WhatsApp's formatting, with an event's
// details added from the database. Sending it is the ordinary Send — the
// groups are recipients like any other. "Open in WhatsApp" and "Copy" are for
// posting somewhere by hand instead.
//
// Closed until somebody asks for it, then it follows the words as they are
// edited, so what is shown is never an older draft than what is on screen.
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import api from '@/api'
import Badge from '@/components/ds/Badge.vue'
import Button from '@/components/ds/Button.vue'
import { problem } from '@/stores/whatsapp'

const props = defineProps({
  heading: { type: String, default: '' },
  body: { type: String, default: '' },
  eventId: { type: Number, default: null },
  // A campaign kind. "on_sale" gives the standard opening and the shop link.
  kind: { type: String, default: null },
  // Open from the start — where a WhatsApp group is ticked, the post is part
  // of what is being sent and should be read before it goes.
  open: { type: Boolean, default: false },
  // What went to WhatsApp with a queued email: [{group, status, error}].
  posted: { type: Array, default: () => [] },
})

const shown = ref(false)
const post = ref(null) // { text, url, warning }
const stale = ref(false)
const loading = ref(false)
const error = ref('')
const copied = ref(false)

const empty = computed(() => !props.eventId && !props.heading.trim() && !props.body.trim())
// Not while the text on screen is behind the words.
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

function show() {
  if (shown.value) return
  shown.value = true
  render()
}

watch(() => props.open, (o) => o && !empty.value && show(), { immediate: true })

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
    <div v-for="p in posted" :key="p.group" class="wa__posted">
      <Badge :tone="p.status === 'sent' ? 'success' : p.status === 'failed' ? 'danger' : 'accent'">{{ p.status }}</Badge>
      <span>{{ p.group }}<template v-if="p.error"> — {{ p.error }}</template></span>
    </div>

    <template v-if="!shown">
      <p class="wa__help">
        What a WhatsApp group gets: the same words in WhatsApp's formatting<template v-if="eventId">,
        with the date, venue, prices and link added from the event</template>.
      </p>
      <div>
        <Button size="sm" variant="secondary" :disabled="empty" @click="show">WhatsApp version</Button>
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
          variant="secondary"
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
        <span v-if="stale || loading" class="wa__help">Updating…</span>
      </div>
      <p class="wa__help">
        Tick a WhatsApp group under “Who gets it” and Send posts this into it.
        Open in WhatsApp or Copy are for posting somewhere else by hand.
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

.wa__posted {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: var(--space-3);
  align-items: start;
  font: var(--type-small);
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
