<script setup>
// The society's WhatsApp number, linked to the site — and where its posts go.
//
// Linking makes the site one of the number's linked devices, as WhatsApp Web
// is (services/whatsapp.py and backend/whatsapp say why, and why carefully).
// Once linked, the groups it may post into are enabled here, laid out as the
// phone shows them: each community with its announcements channel and its
// groups, each a place of its own. A post then picks among the enabled groups.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import api from '@/api'
import Badge from '@/components/ds/Badge.vue'
import Button from '@/components/ds/Button.vue'
import Input from '@/components/ds/Input.vue'
import { groupLabel, loadWhatsApp, problem, whatsapp } from '@/stores/whatsapp'

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

/** The groups as the phone shows them: each community, with its
 *  announcements channel first and then its groups; then the groups that
 *  belong to no community. A community itself is a heading, not a place to
 *  post — its announcements channel and its groups are. */
const sections = computed(() => {
  const all = data.value?.groups ?? []
  const communities = new Map(all.filter((g) => g.community).map((g) => [g.jid, g]))
  const members = new Map()
  const loose = []
  for (const g of all) {
    if (g.community) continue
    const parent = g.parent?.jid
    if (!parent) {
      loose.push(g)
      continue
    }
    if (!members.has(parent)) members.set(parent, [])
    members.get(parent).push(g)
  }
  const out = []
  for (const [jid, groups] of members) {
    groups.sort((a, b) => b.communityAnnouncements - a.communityAnnouncements || a.name.localeCompare(b.name))
    out.push({ key: jid, title: communities.get(jid)?.name || groups[0].parent?.name || 'Community', community: true, groups })
  }
  out.sort((a, b) => a.title.localeCompare(b.title))
  if (loose.length) out.push({ key: 'groups', title: 'Groups', community: false, groups: loose })
  return out
})

/** On screen in a community's section: its channel is "Announcements". */
function rowLabel(g) {
  return g.communityAnnouncements ? 'Announcements' : g.name
}

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
    error.value = problem(e)
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
  // Staging can be linked to the society's real number, and then its groups
  // are the real community: say so before one is made postable.
  if (
    !g.chosen &&
    data.value?.environment === 'staging' &&
    !confirm(
      `This is staging, but the linked number is real: a test post here goes into ` +
        `“${groupLabel(g)}”, to its ${g.size} members.\n\nEnable it only if it's a group for testing.`,
    )
  ) {
    return
  }
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
      <span class="wl__label">Groups the site may post into</span>
      <p class="wl__help">
        Enable the ones announcements can go to. Each post then picks which of
        them it goes into — nothing is picked for it.
      </p>
      <p v-if="data.allowlist" class="wl__help">
        This server also only posts into groups on WHATSAPP_GROUP_ALLOWLIST.
      </p>
      <div v-for="sec in sections" :key="sec.key" class="wl__section">
        <span class="wl__section-title">
          {{ sec.title }}<span v-if="sec.community" class="wl__meta wl__inline"> · community</span>
        </span>
        <label
          v-for="g in sec.groups"
          :key="g.jid"
          class="wl__group"
          :class="{ 'wl__group--off': !g.canPost }"
        >
          <input
            type="checkbox"
            :checked="g.chosen"
            :disabled="busy || (!g.canPost && !g.chosen)"
            @change="toggle(g)"
          />
          <span>
            <strong>{{ rowLabel(g) }}</strong>
            <span class="wl__meta">
              <template v-if="g.communityAnnouncements">everyone in the community · </template>{{ g.size }} people<template v-if="g.adminsOnly"> · admins only</template>
              <template v-if="!g.canPost"> · can't post: only admins can, and this number isn't one</template>
              <template v-if="g.chosen && !g.allowed"> · not on WHATSAPP_GROUP_ALLOWLIST: <span class="wl__mono">{{ g.jid }}</span></template>
            </span>
          </span>
        </label>
        <p v-if="sec.community" class="wl__help">
          Only the community's groups this number is in are listed. To post
          into another of them, add the number to that group on the phone.
        </p>
      </div>
      <p v-if="!sections.length" class="wl__help">The number isn't in any group.</p>
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

.wl__group--off {
  cursor: default;
  opacity: 0.6;
}

.wl__section {
  display: grid;
  gap: var(--space-2);
  padding: var(--space-3) 0;
  border-top: 1px solid var(--border-faint);
}

.wl__section-title {
  font: var(--type-small);
  font-weight: 700;
  color: var(--text-strong);
}

.wl__meta.wl__inline {
  display: inline;
  font-weight: 400;
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
