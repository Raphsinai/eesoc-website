<script setup>
// Where a committee-made passkey link lands.
//
// For somebody our sign-in codes do not reach — usually a sponsor whose mail
// server quarantines them. The committee sends this link by some other route;
// opening it and saving a passkey signs them in, and from then on the passkey
// is how they get in.
//
// The token is in the URL fragment (#…), which the browser never sends to a
// server, and it is posted to the api in a body — so it is in no access log.
// Nothing happens on load: a link scanner that fetches the page uses nothing,
// and saving the passkey takes a press (browsers insist on one anyway).
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import api from '@/api'
import Button from '@/components/ds/Button.vue'
import Card from '@/components/ds/Card.vue'
import Field from '@/components/ds/Field.vue'
import Input from '@/components/ds/Input.vue'
import MaintenanceNotice from '@/components/MaintenanceNotice.vue'
import session, { registerWithLink, saveName } from '@/stores/session'
import { available, reason } from '@/stores/status'
import { passkeyError, passkeysSupported } from '@/utils/passkey'

const route = useRoute()
const router = useRouter()

const token = String(route.hash || '').replace(/^#/, '')
const link = ref(null)
const state = ref('loading') // loading | invalid | ready | name | done
const problem = ref('')
const error = ref('')
const busy = ref(false)
const firstName = ref('')
const lastName = ref('')

// Already signed in as somebody else on this browser: saving the passkey
// switches the browser to the link's account, which is worth saying first.
const otherAccount = computed(
  () => session.authenticated
    && link.value
    && session.user?.email?.toLowerCase() !== link.value.email.toLowerCase()
    ? session.user.email
    : null,
)

function when(value) {
  return new Date(value).toLocaleDateString('en-GB', {
    weekday: 'short', day: 'numeric', month: 'short',
  })
}

onMounted(async () => {
  if (!token) {
    state.value = 'invalid'
    problem.value = 'This link is incomplete. Copy the whole link from the message you were sent.'
    return
  }
  try {
    link.value = await api.post('/auth/passkey-link', { token })
    state.value = 'ready'
  } catch (e) {
    state.value = 'invalid'
    problem.value = e.message
  }
})

async function save() {
  error.value = ''
  busy.value = true
  try {
    await registerWithLink(token)
    // Spent: take the token out of the address bar so it is not bookmarked
    // or shared by accident.
    router.replace({ path: route.path, hash: '' })
    state.value = session.needsName ? 'name' : 'done'
  } catch (e) {
    error.value = passkeyError(e)
  } finally {
    busy.value = false
  }
}

async function submitName() {
  busy.value = true
  try {
    await saveName(firstName.value.trim(), lastName.value.trim())
  } catch {
    // The name is a nicety; they are signed in either way.
  } finally {
    busy.value = false
    state.value = 'done'
  }
}
</script>

<template>
  <main class="setup">
    <Card variant="offset">
      <div style="display:grid; gap:var(--space-5)">
        <span class="eyebrow">EESoc sign-in</span>

        <template v-if="state === 'loading'">
          <p style="margin:0; font:var(--type-body); color:var(--text-muted)">Checking your link…</p>
        </template>

        <template v-else-if="state === 'invalid'">
          <h1 class="setup__title">This link can't be used</h1>
          <p style="margin:0; font:var(--type-body)">{{ problem }}</p>
        </template>

        <template v-else-if="state === 'ready'">
          <h1 class="setup__title">Set up a passkey</h1>
          <p style="margin:0; font:var(--type-body)">
            This sets up sign-in for <strong style="overflow-wrap:anywhere">{{ link.email }}</strong>.
            Your device saves a passkey and signs you in with your fingerprint, face
            or screen lock — no emailed code needed, now or later.
          </p>
          <p style="margin:0; font:var(--type-small); color:var(--text-muted)">
            <template v-if="link.madeBy">{{ link.madeBy }} from the EESoc committee made this link for you.</template>
            <template v-else>The EESoc committee made this link for you.</template>
            It works once, until {{ when(link.expiresAt) }}.
            Not your address? Don't continue — ask them for a new link.
          </p>

          <MaintenanceNotice v-if="!available('passkeys')" :reason="reason('passkeys')" title="Passkeys are unavailable" />
          <p v-else-if="!passkeysSupported()" style="margin:0; font:var(--type-small); color:var(--danger)">
            This browser can't make passkeys. Open the link in a recent Chrome,
            Safari, Edge or Firefox — or on your phone.
          </p>
          <template v-else>
            <p v-if="otherAccount" style="margin:0; font:var(--type-small); color:var(--text-muted)">
              You're signed in here as {{ otherAccount }}. Continuing signs this browser in as {{ link.email }} instead.
            </p>
            <Field :error="error">
              <Button block :disabled="busy" @click="save">
                {{ busy ? 'Waiting for your device…' : 'Save a passkey and sign in' }}
              </Button>
            </Field>
          </template>
        </template>

        <form v-else-if="state === 'name'" style="display:grid; gap:var(--space-4)" @submit.prevent="submitName">
          <h1 class="setup__title">You're signed in</h1>
          <p style="margin:0; font:var(--type-small); color:var(--text-muted)">What should we call you?</p>
          <Field label="First name"><Input v-model="firstName" autocomplete="given-name" /></Field>
          <Field label="Surname"><Input v-model="lastName" autocomplete="family-name" /></Field>
          <Button block type="submit" :disabled="busy">Done</Button>
        </form>

        <template v-else-if="state === 'done'">
          <h1 class="setup__title">You're signed in</h1>
          <p style="margin:0; font:var(--type-body)">
            Your passkey is saved. Next time, press <strong>Log in</strong> and
            choose <strong>Sign in with a passkey</strong>.
          </p>
          <div style="display:flex; gap:var(--space-3); flex-wrap:wrap">
            <Button v-if="session.isSponsor" @click="router.push('/portal')">Go to the sponsor portal</Button>
            <Button v-else @click="router.push('/events')">See what's on</Button>
            <Button variant="ghost" @click="router.push('/account')">Your account</Button>
          </div>
        </template>
      </div>
    </Card>
  </main>
</template>

<style scoped>
.setup {
  max-width: 62ch;
  margin: 0 auto;
  padding: var(--space-12) var(--space-6);
  min-height: 60vh;
}
.setup__title {
  margin: 0;
  font: var(--type-h3);
  color: var(--text-strong);
}
.eyebrow {
  font: var(--type-eyebrow);
  letter-spacing: var(--tracking-eyebrow);
  text-transform: uppercase;
  color: var(--text-accent);
}
</style>
