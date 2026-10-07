<script setup>
// Where the newsletter confirmation link lands. Confirms on a button press
// rather than on load, so a link scanner pre-fetching the URL does not sign
// anyone up.
import { ref } from 'vue'
import { useRoute } from 'vue-router'

import api from '@/api'
import Button from '@/components/ds/Button.vue'
import Card from '@/components/ds/Card.vue'

const route = useRoute()
const busy = ref(false)
const message = ref('')
const error = ref('')
const address = String(route.query.e || '')

async function confirm() {
  busy.value = true
  error.value = ''
  try {
    const res = await api.post('/email/subscribe/confirm', null, {
      e: route.query.e,
      t: route.query.t,
    })
    message.value = res.message
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main style="max-width:var(--container-md, 720px); margin:0 auto; padding:var(--space-10) var(--space-6); min-height:60vh">
    <Card variant="offset">
      <div style="display:grid; gap:var(--space-4)">
        <h1 style="margin:0; font:var(--type-h3)">The EESoc newsletter</h1>
        <template v-if="message">
          <p style="margin:0; font:var(--type-body)">{{ message }}</p>
          <RouterLink to="/events">See what's on</RouterLink>
        </template>
        <template v-else>
          <p style="margin:0; font:var(--type-body)">
            Send the newsletter to <strong>{{ address || 'this address' }}</strong>?
          </p>
          <div>
            <Button :disabled="busy || !address" @click="confirm">
              {{ busy ? 'Confirming…' : 'Yes, sign me up' }}
            </Button>
          </div>
        </template>
        <p v-if="error" style="margin:0; font:var(--type-small); color:var(--danger)">{{ error }}</p>
      </div>
    </Card>
  </main>
</template>
