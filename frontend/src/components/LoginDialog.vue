<script setup>
// Two-step sign-in: email, then the code we send to it.
//
// One form for everybody. The code proves you control the address; whether you
// are a Union member is decided server-side by looking that verified address up
// in the membership feed. Nothing here branches on the email domain — alumni
// and staff have @imperial.ac.uk addresses without being members.
import { computed, nextTick, ref, watch } from 'vue'

import Dialog from '@/components/ds/Dialog.vue'
import Field from '@/components/ds/Field.vue'
import Input from '@/components/ds/Input.vue'
import Button from '@/components/ds/Button.vue'
import session, { devLogin, requestCode, saveName, verifyCode } from '@/stores/session'
import MaintenanceNotice from '@/components/MaintenanceNotice.vue'
import { available, reason } from '@/stores/status'

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

// The name is asked for AFTER verification, and only when the server says it is
// needed — external, no name on file, not a sponsor. Deciding that up front
// would mean telling the browser whether an address already has an account,
// which is an account-enumeration oracle.

function reset() {
  step.value = 'email'
  code.value = ''
  error.value = ''
  notice.value = ''
  busy.value = false
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
    emit('signed-in')
    close()
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
    emit('signed-in')
    close()
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
    :title="step === 'email' ? 'Log in' : step === 'code' ? 'Enter your code' : 'What should we call you?'"
    @close="close"
  >
    <!-- Sign-in codes are email. With no way to send them the form can only
         waste somebody's time, so it is replaced rather than disabled: a
         disabled field invites a retry, an explanation does not. -->
    <div v-if="!available('login')" style="display:grid; gap:var(--space-5)">
      <MaintenanceNotice :reason="reason('login')" title="Sign-in is unavailable" />
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
        @click="() => { emit('signed-in'); close() }"
      >
        Skip for now
      </button>
    </form>

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
