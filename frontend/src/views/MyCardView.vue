<script setup>
// Your own entry on the committee page.
//
// Any committee member can edit their intro and photo here, with or without an
// admin role. Name and post come from ICU's roster and are read-only; where the
// card sits on the grid is set by an admin, so the rows stay even.
import { onMounted, ref } from 'vue'

import api from '@/api'
import Button from '@/components/ds/Button.vue'
import Card from '@/components/ds/Card.vue'
import Field from '@/components/ds/Field.vue'
import TraceRule from '@/components/ds/TraceRule.vue'

const card = ref(null)
const loading = ref(true)
const busy = ref(false)
const error = ref('')
const notice = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    card.value = (await api.get('/committee/me')).card
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

onMounted(load)

async function save() {
  if (!card.value) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    const res = await api.put('/committee/me', {
      intro: card.value.intro ?? '',
      photo_url: card.value.photoUrl ?? null,
    })
    card.value = res.card
    notice.value = 'Saved. It is live on the committee page.'
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

async function uploadPhoto(event) {
  const file = event.target.files?.[0]
  if (!file) return
  busy.value = true
  error.value = ''
  try {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch('/api/committee/me/photo', {
      method: 'POST',
      body: form,
      credentials: 'same-origin',
      headers: { 'X-CSRF-Token': document.cookie.match(/(?:^|;\s*)csrf=([^;]*)/)?.[1] || '' },
    })
    const row = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(row?.detail || 'Upload failed')
    card.value.photoUrl = row.url
    busy.value = false
    await save()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
    event.target.value = ''
  }
}
</script>

<template>
  <main style="max-width:var(--container-lg); margin:0 auto; padding:var(--space-10) var(--space-6); min-height:70vh">
    <header style="display:grid; gap:var(--space-4)">
      <span style="font-family:var(--font-mono); font-size:var(--text-2xs); letter-spacing:var(--tracking-label); text-transform:uppercase; color:var(--text-faint)">
        Committee
      </span>
      <h1 style="margin:0; font:var(--type-h1)">My card</h1>
      <p style="margin:0; font:var(--type-small); color:var(--text-muted); max-width:70ch">
        Your entry on the committee page. Your name and Union role come from
        ICU's records and can't be edited here. Where you sit on the page is set
        by an admin.
      </p>
    </header>

    <TraceRule style="margin:var(--space-8) 0" />

    <p v-if="error" style="font:var(--type-small); color:var(--danger)">{{ error }}</p>
    <p v-if="notice" style="font:var(--type-small); color:var(--text-muted)">{{ notice }}</p>

    <Card v-if="!loading && card" variant="offset" style="max-width:62ch">
      <div class="my-card">
        <div>
          <img
            v-if="card.photoUrl"
            :src="card.photoUrl"
            :alt="card.name"
            style="width:112px; height:112px; object-fit:cover; border-radius:var(--radius-sm)"
          />
          <div v-else style="width:112px; height:112px; background:var(--surface-sunken); border-radius:var(--radius-sm)" />
          <label style="display:block; margin-top:var(--space-2); font:var(--type-2xs); color:var(--text-muted)">
            Change photo
            <input
              type="file"
              accept="image/jpeg,image/png,image/webp"
              :disabled="busy"
              style="display:block; margin-top:4px; font-size:10px; width:112px"
              @change="uploadPhoto"
            />
          </label>
        </div>

        <div style="display:grid; gap:var(--space-4); min-width:0">
          <div>
            <strong style="font:var(--type-h4)">{{ card.name }}</strong>
            <div style="font-family:var(--font-mono); font-size:var(--text-2xs); color:var(--text-faint)">
              {{ card.displayRole || card.unionRole }}
            </div>
          </div>

          <Field label="Intro" help="A sentence or two. This is what shows under your photo.">
            <textarea v-model="card.intro" rows="4" class="ees-textarea" />
          </Field>

          <div>
            <Button :disabled="busy" @click="save">{{ busy ? 'Saving…' : 'Save my card' }}</Button>
          </div>
        </div>
      </div>
    </Card>

    <Card v-else-if="!loading" variant="outline">
      <p style="margin:0; font:var(--type-small); color:var(--text-muted)">
        We can't find you on this year's committee roster. If you are on it, ask
        an admin to link your email to your card.
      </p>
    </Card>
  </main>
</template>

<style scoped>
.my-card {
  display: grid;
  grid-template-columns: 112px minmax(0, 1fr);
  gap: var(--space-5);
  align-items: start;
}

@media (max-width: 520px) {
  .my-card {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
