<script setup>
// Your account: who you are signed in as, and your passkeys.
//
// The passkeys list is the half that matters. A passkey is a way into the
// account for as long as it exists, so somebody who loses a phone, or finds
// one here they do not recognise, must be able to see it and remove it —
// including one that came from a link the committee made, which says so.
import { computed, inject, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import api from '@/api'
import Badge from '@/components/ds/Badge.vue'
import Button from '@/components/ds/Button.vue'
import Card from '@/components/ds/Card.vue'
import session, { addPasskey, refresh } from '@/stores/session'
import { available } from '@/stores/status'
import { passkeyError, passkeysSupported } from '@/utils/passkey'

const router = useRouter()
const openLogin = inject('openLogin', () => {})

const passkeys = ref([])
const loaded = ref(false)
const busy = ref(false)
const error = ref('')
const notice = ref('')

// The server refuses a passkey made while impersonating; not offering the
// button says why before anybody presses it.
const canAdd = computed(
  () => passkeysSupported() && available('passkeys') && !session.impersonating,
)

function when(value) {
  if (!value) return null
  return new Date(value).toLocaleDateString('en-GB', {
    day: 'numeric', month: 'short', year: 'numeric',
  })
}

async function load() {
  if (!session.authenticated) return
  try {
    passkeys.value = (await api.get('/auth/passkeys')).passkeys
  } catch (e) {
    error.value = e.message
  } finally {
    loaded.value = true
  }
}

async function add() {
  error.value = ''
  notice.value = ''
  busy.value = true
  try {
    await addPasskey()
    notice.value = 'Passkey saved. You can use it the next time you sign in.'
    await load()
  } catch (e) {
    error.value = passkeyError(e)
  } finally {
    busy.value = false
  }
}

async function remove(p) {
  const synced = p.synced
    ? ' It is synced through your password manager, so it goes from your other devices too.'
    : ''
  if (!confirm(`Remove the passkey “${p.name}”?${synced} You can still sign in with an emailed code.`)) return
  error.value = ''
  notice.value = ''
  busy.value = true
  try {
    await api.del(`/auth/passkeys/${p.id}`)
    notice.value = 'Passkey removed. Your device may still offer it — it will no longer work here, and you can delete it in your password manager.'
    await Promise.all([load(), refresh()])
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

async function logoutEverywhere() {
  if (!confirm('Sign out of every browser and device, including this one?')) return
  busy.value = true
  try {
    await api.post('/auth/logout-everywhere')
  } finally {
    await refresh()
    busy.value = false
    router.push('/')
  }
}

onMounted(load)
// Signing in from this page (the button below) should show the list without a
// reload.
watch(() => session.authenticated, (yes) => { if (yes) load() })
</script>

<template>
  <main class="account">
    <span class="eyebrow">Your account</span>
    <h1 style="margin:0; font:var(--type-h1); letter-spacing:var(--tracking-display); color:var(--text-strong)">Account</h1>

    <template v-if="!session.loaded" />

    <Card v-else-if="!session.authenticated" variant="outline">
      <div style="display:grid; gap:var(--space-4)">
        <p style="margin:0; font:var(--type-body)">Sign in to see your account and passkeys.</p>
        <div><Button @click="openLogin()">Log in</Button></div>
      </div>
    </Card>

    <template v-else>
      <Card variant="outline">
        <div style="display:grid; gap:var(--space-2)">
          <span style="font:var(--type-h5); color:var(--text-strong)">{{ session.user?.name || session.user?.email }}</span>
          <span style="font-family:var(--font-mono); font-size:var(--text-xs); color:var(--text-muted); overflow-wrap:anywhere">{{ session.user?.email }}</span>
          <div style="display:flex; gap:var(--space-2); flex-wrap:wrap">
            <Badge v-if="session.isMember" tone="success">Member {{ session.academicYear }}</Badge>
            <Badge v-if="session.isSponsor" tone="brand">Sponsor</Badge>
            <Badge v-if="session.role" tone="accent">{{ session.role }}</Badge>
          </div>
        </div>
      </Card>

      <Card variant="offset">
        <div style="display:grid; gap:var(--space-4)">
          <h2 style="margin:0; font:var(--type-h4)">Passkeys</h2>
          <p style="margin:0; font:var(--type-small); color:var(--text-muted)">
            A passkey signs you in with your fingerprint, face or screen lock
            instead of an emailed code. Your device or password manager holds
            it; we only keep a public key, which cannot sign anybody in.
          </p>

          <div v-if="loaded && !passkeys.length" style="font:var(--type-small); color:var(--text-faint)">
            No passkeys yet — you sign in with an emailed code.
          </div>

          <div
            v-for="p in passkeys"
            :key="p.id"
            class="passkey-row"
          >
            <div style="display:grid; gap:2px; min-width:0">
              <span style="font:var(--type-small); color:var(--text-strong)">
                {{ p.name }}
                <Badge v-if="p.synced" tone="neutral">synced</Badge>
              </span>
              <span style="font:var(--type-2xs); color:var(--text-faint)">
                Added {{ when(p.createdAt) }}<template v-if="p.viaLink"> through a setup link<template v-if="p.linkMadeBy"> from {{ p.linkMadeBy }}</template></template>
                · {{ p.lastUsedAt ? `last used ${when(p.lastUsedAt)}` : 'never used to sign in' }}
              </span>
            </div>
            <Button size="sm" variant="ghost" :disabled="busy || session.impersonating" @click="remove(p)">Remove</Button>
          </div>

          <div v-if="canAdd">
            <Button :disabled="busy" @click="add">
              {{ busy ? 'Waiting for your device…' : passkeys.length ? 'Add another passkey' : 'Save a passkey' }}
            </Button>
          </div>
          <p v-else-if="session.impersonating" style="margin:0; font:var(--type-2xs); color:var(--text-faint)">
            Passkeys can't be added or removed while you are signed in as somebody else.
          </p>
          <p v-else-if="!passkeysSupported()" style="margin:0; font:var(--type-2xs); color:var(--text-faint)">
            This browser can't make passkeys. Try a recent Chrome, Safari, Edge or Firefox.
          </p>

          <p v-if="notice" style="margin:0; font:var(--type-small); color:var(--text-muted)">{{ notice }}</p>
          <p v-if="error" style="margin:0; font:var(--type-small); color:var(--danger)">{{ error }}</p>
        </div>
      </Card>

      <Card variant="outline">
        <div style="display:grid; gap:var(--space-3)">
          <h2 style="margin:0; font:var(--type-h4)">Signed-in devices</h2>
          <p style="margin:0; font:var(--type-small); color:var(--text-muted)">
            Lost a laptop, or signed in on a shared computer? This signs every
            browser out, this one included.
          </p>
          <div>
            <Button variant="secondary" size="sm" :disabled="busy || session.impersonating" @click="logoutEverywhere">
              Sign out everywhere
            </Button>
          </div>
        </div>
      </Card>
    </template>
  </main>
</template>

<style scoped>
.account {
  max-width: 62ch;
  margin: 0 auto;
  padding: var(--space-12) var(--space-6);
  display: grid;
  gap: var(--space-6);
  min-height: 60vh;
  align-content: start;
}
.eyebrow {
  font: var(--type-eyebrow);
  letter-spacing: var(--tracking-eyebrow);
  text-transform: uppercase;
  color: var(--text-accent);
}
.passkey-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) 0;
  border-top: 1px solid var(--border-hairline);
}
</style>
