<script setup>
// The society's WhatsApp number, linked to the site — and where its posts go.
//
// Linking makes the site one of the number's linked devices, as WhatsApp Web
// is (services/whatsapp.py and backend/whatsapp say why, and why carefully).
// Once linked, the groups it may post into are chosen here; every "WhatsApp
// version" block can then post to them, and an email in the queue can be
// ticked to go to them when it is sent.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import api from '@/api'
import Badge from '@/components/ds/Badge.vue'
import Button from '@/components/ds/Button.vue'
import Input from '@/components/ds/Input.vue'
import { loadWhatsApp, whatsapp } from '@/stores/whatsapp'

const busy = ref(false)
const error = ref('')
const phone = ref('')
const byCode = ref(false)

const data = computed(() => whatsapp.data)
const status = computed(() => data.value?.status)
const state = computed(() => status.value?.state ?? null)

const BADGES = {
  connected: ['success', 'linked'],
  linking: ['brand', 'linking'],
  connecting: ['accent', 'connecting'],
  reconnecting: ['accent', 'reconnecting'],
  unlinked: ['neutral', 'not linked'],
}
// Not configured is a choice (production until its overlay turns it on), not
// a fault, so it is not shown in red.
const badge = computed(() => {
  if (data.value && !data.value.configured) return ['neutral', 'off']
  return status.value ? BADGES[state.value] ?? ['neutral', state.value] : ['danger', 'not running']
})

/** A community's announcements group is named after the community; say whose
 *  it is, so it is not mistaken for an ordinary group of the same name. */
function label(g) {
  return g.communityAnnouncements && g.parent?.name ? `${g.name} — ${g.parent.name} announcements` : g.name
}

const postable = computed(() => (data.value?.groups ?? []).filter((g) => g.canPost))
const notPostable = computed(() => (data.value?.groups ?? []).filter((g) => !g.canPost && !g.community))

// While linking, the QR code rotates and the phone may confirm at any moment,
// so look again every few seconds — and only then.
let timer = null
watch(
  state,
  (s) => {
    clearInterval(timer)
    if (['linking', 'connecting', 'reconnecting'].includes(s)) timer = setInterval(loadWhatsApp, 3000)
  },
  { immediate: true },
)
onMounted(loadWhatsApp)
onBeforeUnmount(() => clearInterval(timer))

async function act(fn) {
  busy.value = true
  error.value = ''
  try {
    await fn()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
    await loadWhatsApp()
  }
}

/** UK numbers are typed as 07…; WhatsApp wants 447…. */
function international(raw) {
  let digits = raw.replace(/\D/g, '')
  if (digits.startsWith('00')) digits = digits.slice(2)
  else if (digits.startsWith('0')) digits = `44${digits.slice(1)}`
  return digits
}

function startLink() {
  act(() => api.post('/admin/whatsapp/link', byCode.value ? { phone: international(phone.value) } : {}))
}

function unlink() {
  const msg =
    state.value === 'connected'
      ? 'Unlink the society\'s WhatsApp number from the site? Nothing more is posted until it is linked again.'
      : 'Stop linking?'
  if (!confirm(msg)) return
  act(() => api.post('/admin/whatsapp/unlink'))
}

function toggle(g) {
  act(() => api.put('/admin/whatsapp/groups', { jid: g.jid, chosen: !g.chosen }))
}
</script>

<template>
  <div class="wl">
    <div class="wl__status">
      <Badge :tone="badge[0]">{{ badge[1] }}</Badge>
      <span v-if="state === 'connected'" style="font:var(--type-small)">
        {{ status.me?.name || 'WhatsApp' }}
        <span class="wl__mono">{{ status.me?.id?.split('@')[0] }}</span>
      </span>
      <span v-else-if="data && !data.configured" style="font:var(--type-small); color:var(--text-muted)">
        Not switched on here. Posting by hand with “Open in WhatsApp” still works.
      </span>
      <Button
        v-if="['connected', 'linking', 'reconnecting', 'connecting'].includes(state)"
        size="sm"
        variant="ghost"
        :disabled="busy"
        @click="unlink"
      >
        {{ state === 'linking' ? 'Cancel' : 'Unlink' }}
      </Button>
    </div>

    <p v-if="data?.problem && data.configured" class="wl__error">{{ data.problem }}</p>
    <p v-if="status?.error" class="wl__error">{{ status.error }}</p>
    <p v-if="whatsapp.error" class="wl__error">{{ whatsapp.error }}</p>

    <!-- ----------------------------------------------------- not linked -->
    <template v-if="state === 'unlinked'">
      <p class="wl__help">
        Link the society's WhatsApp Business number and the site can post
        announcements straight into its groups and community — when you press
        Post, or with an email at the time you approved it for.
      </p>
      <p class="wl__risk">
        This makes the site a linked device on that number, like WhatsApp Web.
        WhatsApp doesn't allow automating that, and if it decides this is a
        bot it bans the number — the one your community runs on. The site
        keeps to a few posts an hour, a few seconds apart, only in groups you
        choose here; keep it to announcements.
      </p>
      <label class="wl__choice">
        <input v-model="byCode" type="checkbox" />
        <span>Link with a code instead of a QR code (for when the phone is the screen you're on)</span>
      </label>
      <Input
        v-if="byCode"
        v-model="phone"
        type="tel"
        placeholder="The society's number, e.g. 07700 900123"
        aria-label="WhatsApp number to link"
      />
      <div>
        <Button size="sm" :disabled="busy || (byCode && phone.replace(/\D/g, '').length < 8)" @click="startLink">
          {{ busy ? 'Starting…' : 'Link a number' }}
        </Button>
      </div>
    </template>

    <!-- -------------------------------------------------------- linking -->
    <template v-else-if="state === 'linking'">
      <template v-if="status.pairingCode">
        <p class="wl__help">
          On the society's phone: WhatsApp Business → Settings → Linked devices
          → Link a device → <strong>Link with phone number instead</strong>, and
          enter:
        </p>
        <p class="wl__code">{{ status.pairingCode }}</p>
      </template>
      <template v-else-if="status.qr">
        <p class="wl__help">
          On the society's phone: WhatsApp Business → Settings → Linked devices
          → Link a device, and scan this. It changes every few seconds.
        </p>
        <img class="wl__qr" :src="status.qr" alt="QR code to link WhatsApp" />
      </template>
      <p v-else class="wl__help">Asking WhatsApp for a code…</p>
    </template>

    <!-- ------------------------------------------------------ connected -->
    <template v-else-if="state === 'connected' && data.groups">
      <span class="wl__label">Post announcements into</span>
      <p v-if="data.allowlist" class="wl__help">
        This environment posts only into groups on WHATSAPP_GROUP_ALLOWLIST
        <template v-if="!data.allowlist.length">— which is empty, so nowhere.</template>
        Add a test group's id there to try it.
      </p>
      <label v-for="g in postable" :key="g.jid" class="wl__group">
        <input type="checkbox" :checked="g.chosen" :disabled="busy" @change="toggle(g)" />
        <span>
          <strong>{{ label(g) }}</strong>
          <span class="wl__meta">
            {{ g.size }} people<template v-if="g.adminsOnly"> · admins only</template>
            <template v-if="!g.allowed"> · not allowed here: <span class="wl__mono">{{ g.jid }}</span></template>
          </span>
        </span>
      </label>
      <p v-if="!postable.length" class="wl__help">The number isn't in any group it can post in.</p>
      <p v-if="notPostable.length" class="wl__help">
        Can't post in {{ notPostable.map(label).join(', ') }} — only admins can,
        and this number isn't one.
      </p>
    </template>

    <template v-if="data?.chosen?.length && state !== 'connected'">
      <span class="wl__label">Chosen groups</span>
      <p class="wl__help">{{ data.chosen.map((g) => g.name || g.jid).join(', ') }} — nothing is posted while it isn't linked.</p>
    </template>

    <template v-if="data?.posts?.length">
      <span class="wl__label">Latest posts</span>
      <div v-for="p in data.posts" :key="p.id" class="wl__post">
        <Badge :tone="p.status === 'sent' ? 'success' : p.status === 'failed' ? 'danger' : 'accent'">{{ p.status }}</Badge>
        <span>
          <strong>{{ p.group }}</strong>
          <span class="wl__meta">{{ new Date(p.createdAt).toLocaleString() }} · {{ p.preview }}</span>
          <span v-if="p.error" class="wl__error">{{ p.error }}</span>
        </span>
      </div>
    </template>

    <p v-if="error" class="wl__error">{{ error }}</p>
  </div>
</template>

<style scoped>
.wl {
  display: grid;
  gap: var(--space-3);
}

.wl__status {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
}

.wl__label {
  margin-top: var(--space-2);
  font: var(--type-label);
  color: var(--text-muted);
}

.wl__help {
  margin: 0;
  font: var(--type-2xs);
  color: var(--text-muted);
}

.wl__risk {
  margin: 0;
  padding: var(--space-3);
  border-left: 3px solid var(--danger);
  font: var(--type-2xs);
  color: var(--text-strong);
}

.wl__error {
  display: block;
  margin: 0;
  font: var(--type-2xs);
  color: var(--danger);
}

.wl__mono {
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  color: var(--text-faint);
  overflow-wrap: anywhere;
}

.wl__code {
  margin: 0;
  font-family: var(--font-mono);
  font-size: 28px;
  letter-spacing: 0.15em;
  color: var(--text-strong);
}

/* A QR code needs a white quiet zone whatever the theme, or phones in dark
   mode fail to read it. */
.wl__qr {
  width: min(260px, 100%);
  padding: 8px;
  background: #fff;
  border-radius: var(--radius-sm);
}

.wl__choice,
.wl__group,
.wl__post {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: var(--space-3);
  align-items: start;
  font: var(--type-small);
}

.wl__group {
  cursor: pointer;
}

.wl__choice input,
.wl__group input {
  margin-top: 3px;
  width: 15px;
  height: 15px;
  accent-color: var(--action-primary);
}

.wl__meta {
  display: block;
  font: var(--type-2xs);
  color: var(--text-muted);
  overflow-wrap: anywhere;
}
</style>
