<script setup>
// "Get the newsletter", for the What's on page.
//
// Signed in: one click, against the account's address. Signed out: type an
// address and we email a confirmation link — nothing is subscribed until it is
// followed (POST /email/subscribe, double opt-in).
import { onMounted, ref } from 'vue'

import api from '@/api'
import Button from '@/components/ds/Button.vue'
import Input from '@/components/ds/Input.vue'
import session from '@/stores/session'

const email = ref('')
const busy = ref(false)
const done = ref('')
const error = ref('')
// null until known; true when a signed-in account already gets it.
const subscribed = ref(null)

onMounted(async () => {
  if (!session.authenticated) return
  try {
    const res = await api.get('/email/topics')
    subscribed.value = Boolean(res.topics.find((t) => t.key === 'newsletter')?.subscribed)
  } catch {
    subscribed.value = null
  }
})

async function submit() {
  busy.value = true
  error.value = ''
  try {
    const res = await api.post('/email/subscribe', {
      email: session.authenticated ? '' : email.value.trim(),
    })
    done.value = res.message
    if (res.subscribed) subscribed.value = true
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section class="newsletter">
    <div style="display:grid; gap:4px; min-width:0">
      <strong style="font:var(--type-h5); color:var(--text-strong)">Never miss an event</strong>
      <span style="font:var(--type-small); color:var(--text-muted)">
        <template v-if="subscribed">
          You're getting the newsletter at {{ session.user?.email }}.
          <RouterLink to="/email">Change what you get</RouterLink>
        </template>
        <template v-else>
          The newsletter: what's on, and when tickets go on sale. Unsubscribe any time.
        </template>
      </span>
    </div>

    <p v-if="done" class="newsletter__done">{{ done }}</p>

    <form v-else-if="!subscribed" class="newsletter__form" @submit.prevent="submit">
      <Input
        v-if="!session.authenticated"
        v-model="email"
        type="email"
        placeholder="you@ic.ac.uk"
        aria-label="Email address"
        required
      />
      <Button type="submit" :disabled="busy || (!session.authenticated && !email.includes('@'))">
        {{ busy ? 'Signing up…' : 'Get the newsletter' }}
      </Button>
    </form>

    <p v-if="error" style="margin:0; grid-column:1 / -1; font:var(--type-small); color:var(--danger)">{{ error }}</p>
  </section>
</template>

<style scoped>
.newsletter {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: var(--space-4) var(--space-6);
  align-items: center;
  padding: var(--space-5) var(--space-6);
  border: var(--border-width-hairline) solid var(--border-default);
  border-radius: var(--radius-md);
  background: var(--surface-card);
}

.newsletter__form {
  display: flex;
  gap: var(--space-3);
  align-items: center;
}

.newsletter__form :deep(.ees-input) {
  min-width: 220px;
}

.newsletter__done {
  margin: 0;
  font: var(--type-small);
  color: var(--text-strong);
}

@media (max-width: 720px) {
  .newsletter {
    grid-template-columns: minmax(0, 1fr);
  }

  .newsletter__form {
    flex-wrap: wrap;
  }

  .newsletter__form :deep(.ees-input) {
    min-width: 0;
    flex: 1 1 200px;
  }
}
</style>
