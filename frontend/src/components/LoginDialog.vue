<script setup>
// Two-step sign-in: email, then the code we send to it — or a passkey.
//
// One form for everybody. The code proves you control the address; whether you
// are a Union member is decided server-side by looking that verified address up
// in the membership feed. Nothing here branches on the email domain — alumni
// and staff have @imperial.ac.uk addresses without being members.
//
// A passkey is the way round the email: saved once, after a code sign-in, it
// signs you in with the device's own lock. It still works when the codes do
// not (a sponsor's spam filter, the yearly mailbox lapse), which is why its
// button survives the maintenance notice below.
import { computed, nextTick, ref, watch } from 'vue'

import Dialog from '@/components/ds/Dialog.vue'
import Field from '@/components/ds/Field.vue'
import Input from '@/components/ds/Input.vue'
import Button from '@/components/ds/Button.vue'
import session, {
  addPasskey,
  devLogin,
  passkeySignIn,
  requestCode,
  saveName,
  verifyCode,
} from '@/stores/session'
import MaintenanceNotice from '@/components/MaintenanceNotice.vue'
import { available, reason } from '@/stores/status'
import { passkeyError, passkeysSupported } from '@/utils/passkey'

const props = defineProps({ open: { type: Boolean, default: false } })
const emit = defineEmits(['update:open', 'signed-in'])

const step = ref('email')
const email = ref('')
const code = ref('')
const firstName = ref('')
const lastName = ref('')
const busy = ref(false)
const error = ref('')
const notice = ref('')
const codeInput = ref(null)
// Kept apart from `error`, which sits under the email field and would read as
// a complaint about the address.
const passkeyMessage = ref('')

const canUsePasskeys = computed(() => passkeysSupported() && available('passkeys'))

const TITLES = {
  email: 'Log in',
  code: 'Enter your code',
  name: 'What should we call you?',
  passkey: 'Skip the code next time?',
}

// The name is asked for AFTER verification, and only when the server says it is
// needed — external, no name on file, not a sponsor. Deciding that up front
// would mean telling the browser whether an address already has an account,
// which is an account-enumeration oracle.

function reset() {
  step.value = 'email'
  code.value = ''
  error.value = ''
  notice.value = ''
  passkeyMessage.value = ''
  busy.value = false
}

// "Not now" is remembered on this browser for a month, so somebody who does
// not want a passkey is not asked at every sign-in. Per account, because a
// shared machine is shared. Storage can be missing or throw (private windows,
// blocked site data); then they are simply asked again.
const SNOOZE_MS = 30 * 24 * 3600 * 1000

function snoozeKey() {
  return `eesoc.passkey-offer.${session.user?.id}`
}

function offerSnoozed() {
  try {
    return Number(localStorage.getItem(snoozeKey())) > Date.now()
  } catch {
    return false
  }
}

function snoozeOffer() {
  try {
    localStorage.setItem(snoozeKey(), String(Date.now() + SNOOZE_MS))
  } catch {
    // Nothing to do: they will be asked next time.
  }
}

// After a code sign-in: offer a passkey to somebody who has none, unless they
// said not now. Never while impersonating — the server refuses that anyway.
function finishSignIn() {
  if (
    canUsePasskeys.value
    && session.passkeys === 0
    && !session.impersonating
    && !offerSnoozed()
  ) {
    error.value = ''
    step.value = 'passkey'
    return
  }
  emit('signed-in')
  close()
}

async function signInWithPasskey() {
  passkeyMessage.value = ''
  busy.value = true
  try {
    await passkeySignIn()
    emit('signed-in')
    close()
  } catch (e) {
    passkeyMessage.value = passkeyError(e)
  } finally {
    busy.value = false
  }
}

async function savePasskey() {
  error.value = ''
  busy.value = true
  try {
    await addPasskey()
    emit('signed-in')
    close()
  } catch (e) {
    error.value = passkeyError(e)
  } finally {
    busy.value = false
  }
}

function notNow() {
  snoozeOffer()
  emit('signed-in')
  close()
}

function close() {
  emit('update:open', false)
}

watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) reset()
  },
)

async function sendCode() {
  error.value = ''
  if (!email.value.includes('@')) {
    error.value = 'Enter an email address.'
    return
  }
  busy.value = true
  try {
    const res = await requestCode(email.value.trim(), {
      first_name: firstName.value.trim() || null,
      last_name: lastName.value.trim() || null,
    })
    step.value = 'code'
    notice.value = res?.message || 'Check your inbox.'
    await nextTick()
    codeInput.value?.$el?.querySelector?.('input')?.focus()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

// Development only: the panel is hidden unless the server says the bypass is
// live, and the endpoint 404s otherwise, so this cannot be used in production.
async function devSignIn() {
  error.value = ''
  if (!email.value.includes('@')) {
    error.value = 'Enter an email first.'
    return
  }
  busy.value = true
  try {
    await devLogin(email.value.trim(), true)
    emit('signed-in')
    close()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

async function submitName() {
  busy.value = true
  error.value = ''
  try {
    await saveName(firstName.value.trim(), lastName.value.trim())
    finishSignIn()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

async function submitCode() {
  error.value = ''
  if (!code.value.trim()) {
    error.value = 'Enter the code from your email.'
    return
  }
  busy.value = true
  try {
    await verifyCode(email.value.trim(), code.value.trim())
    // Members take their name from the Union record and sponsors are set up by
    // the committee, so only an unnamed external guest sees this step.
    if (session.needsName) {
      step.value = 'name'
      return
    }
    finishSignIn()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <Dialog
    :open="open"
    :title="TITLES[step]"
    @close="close"
  >
    <!-- Sign-in codes are email. With no way to send them the form can only
         waste somebody's time, so it is replaced rather than disabled: a
         disabled field invites a retry, an explanation does not. -->
    <div v-if="!available('login')" style="display:grid; gap:var(--space-5)">
      <MaintenanceNotice :reason="reason('login')" title="Sign-in is unavailable" />
      <!-- A passkey needs no email, so it still works while codes do not. -->
      <template v-if="canUsePasskeys">
        <p style="margin:0; font:var(--type-small); color:var(--text-muted)">
          If you've saved a passkey, you can still use it.
        </p>
        <Button block type="button" :disabled="busy" @click="signInWithPasskey">
          {{ busy ? 'Waiting for your device…' : 'Sign in with a passkey' }}
        </Button>
        <p v-if="passkeyMessage" style="margin:0; font:var(--type-small); color:var(--danger)">{{ passkeyMessage }}</p>
      </template>
      <p style="margin:0; font:var(--type-small); color:var(--text-muted)">
        Everything you can read without an account still works — events,
        resources, the committee and our sponsors.
      </p>
      <Button block variant="secondary" @click="close">Close</Button>
    </div>

    <form v-else-if="step === 'email'" style="display:grid; gap:var(--space-6)" @submit.prevent="sendCode">
      <p style="margin:0; font:var(--type-small); color:var(--text-muted)">
        Members, sponsors and guests all use the same door. We email you a code —
        no password to forget.
      </p>

      <Field label="Email" :error="error" help="Imperial students: use shortcode@ic.ac.uk.">
        <Input v-model="email" type="email" placeholder="abc123@ic.ac.uk" autocomplete="email" />
      </Field>

      <Button block type="submit" :disabled="busy">
        {{ busy ? 'Sending…' : 'Email me a code' }}
      </Button>

      <template v-if="canUsePasskeys">
        <span class="login-or" aria-hidden="true">or</span>
        <div style="display:grid; gap:var(--space-2)">
          <Button block variant="secondary" type="button" :disabled="busy" @click="signInWithPasskey">
            Sign in with a passkey
          </Button>
          <p v-if="passkeyMessage" style="margin:0; font:var(--type-small); color:var(--danger)">{{ passkeyMessage }}</p>
        </div>
      </template>

      <span style="font-family:var(--font-mono); font-size:var(--text-2xs); letter-spacing:var(--tracking-label); text-transform:uppercase; color:var(--text-faint); text-align:center">
        Membership comes from the Union
      </span>

      <!-- Only rendered while the server reports development mode. -->
      <template v-if="session.devLogin">
        <div style="border-top:1px dashed var(--border-default); padding-top:var(--space-5); display:grid; gap:var(--space-3)">
          <span style="font-family:var(--font-mono); font-size:var(--text-2xs); text-transform:uppercase; letter-spacing:var(--tracking-label); color:var(--danger)">
            Development only
          </span>
          <p style="margin:0; font:var(--type-2xs); color:var(--text-muted)">
            No SMTP configured. This signs you in as the address above, with
            committee access, without a code.
          </p>
          <Button variant="secondary" block type="button" :disabled="busy" @click="devSignIn">
            Skip the code and sign in as admin
          </Button>
        </div>
      </template>
    </form>

    <!-- Only reached by an external guest with no name on file. -->
    <form v-else-if="step === 'name'" style="display:grid; gap:var(--space-6)" @submit.prevent="submitName">
      <p style="margin:0; font:var(--type-small); color:var(--text-muted)">
        You're signed in. We just need a name for your bookings.
      </p>
      <Field label="First name" :error="error">
        <Input v-model="firstName" autocomplete="given-name" />
      </Field>
      <Field label="Surname">
        <Input v-model="lastName" autocomplete="family-name" />
      </Field>
      <Button block type="submit" :disabled="busy">{{ busy ? 'Saving…' : 'Done' }}</Button>
      <button
        type="button"
        style="background:none; border:0; padding:0; cursor:pointer; font:var(--type-2xs); color:var(--text-faint); text-decoration:underline"
        @click="finishSignIn"
      >
        Skip for now
      </button>
    </form>

    <!-- Offered once a code sign-in has worked, to an account with no passkey.
         Optional: "Not now" signs them in all the same. -->
    <div v-else-if="step === 'passkey'" style="display:grid; gap:var(--space-5)">
      <p style="margin:0; font:var(--type-small); color:var(--text-muted)">
        You're signed in. Save a passkey and next time you can sign in with
        your fingerprint, face or screen lock instead of waiting for an email.
      </p>
      <p style="margin:0; font:var(--type-2xs); color:var(--text-faint)">
        Your device or password manager keeps it. You can remove it any time
        from your account page.
      </p>
      <Field :error="error">
        <Button block type="button" :disabled="busy" @click="savePasskey">
          {{ busy ? 'Waiting for your device…' : 'Save a passkey' }}
        </Button>
      </Field>
      <button
        type="button"
        style="background:none; border:0; padding:0; cursor:pointer; font:var(--type-small); color:var(--text-faint); text-decoration:underline"
        :disabled="busy"
        @click="notNow"
      >
        Not now
      </button>
    </div>

    <form v-else style="display:grid; gap:var(--space-6)" @submit.prevent="submitCode">
      <p style="margin:0; font:var(--type-small); color:var(--text-muted)">
        {{ notice }} We sent a code to <strong>{{ email }}</strong>.
      </p>

      <Field label="Six-digit code" :error="error">
        <Input
          ref="codeInput"
          v-model="code"
          inputmode="numeric"
          autocomplete="one-time-code"
          placeholder="123456"
        />
      </Field>

      <Button block type="submit" :disabled="busy">
        {{ busy ? 'Checking…' : 'Log in' }}
      </Button>

      <Button variant="ghost" block type="button" :disabled="busy" @click="sendCode">
        Send another code
      </Button>

      <button
        type="button"
        style="background:none; border:0; padding:0; cursor:pointer; font:var(--type-small); color:var(--text-faint); text-decoration:underline"
        @click="step = 'email'"
      >
        Use a different email
      </button>
    </form>
  </Dialog>
</template>

<style scoped>
/* "or" between the two ways in, with a rule either side. */
.login-or {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  letter-spacing: var(--tracking-label);
  text-transform: uppercase;
  color: var(--text-faint);
}
.login-or::before,
.login-or::after {
  content: '';
  flex: 1;
  border-top: 1px solid var(--border-hairline);
}
</style>
