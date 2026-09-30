<script setup>
// Open roles across every sponsor.
//
// `GET /api/jobs` has existed since the sponsor portal was built but nothing
// rendered it — sponsors could post a role and no student could ever see it.
import { computed, onMounted, ref } from 'vue'

import api from '@/api'
import Card from '@/components/ds/Card.vue'
import Badge from '@/components/ds/Badge.vue'
import Button from '@/components/ds/Button.vue'
import Input from '@/components/ds/Input.vue'
import TraceRule from '@/components/ds/TraceRule.vue'
import { applySeo } from '@/seo'

const jobs = ref([])
const loading = ref(true)
const error = ref('')
const query = ref('')

onMounted(async () => {
  applySeo({
    title: 'Open roles',
    description:
      'Graduate roles, internships and placements from the companies that sponsor EESoc at Imperial College London.',
    path: '/jobs',
  })
  try {
    const data = await api.get('/jobs')
    jobs.value = data.jobs
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
})

const term = computed(() => query.value.trim().toLowerCase())
const filtered = computed(() =>
  !term.value
    ? jobs.value
    : jobs.value.filter((j) =>
        `${j.title} ${j.sponsor || ''} ${j.location || ''}`.toLowerCase().includes(term.value),
      ),
)

const heading = computed(() =>
  loading.value
    ? 'Loading roles…'
    : jobs.value.length
      ? `${jobs.value.length} open role${jobs.value.length === 1 ? '' : 's'}.`
      : 'No roles open right now.',
)
</script>

<template>
  <main style="max-width:var(--container-lg); margin:0 auto; padding:var(--space-10) var(--space-6); min-height:70vh">
    <header style="display:grid; gap:var(--space-4)">
      <span style="font-family:var(--font-mono); font-size:var(--text-2xs); letter-spacing:var(--tracking-label); text-transform:uppercase; color:var(--text-faint)">
        Careers
      </span>
      <h1 style="margin:0; font:var(--type-h1)">{{ heading }}</h1>
      <p style="margin:0; font:var(--type-small); color:var(--text-muted); max-width:70ch">
        Posted directly by the companies that sponsor us. Applications go to them,
        not through EESoc — we don't see who applies.
      </p>
    </header>

    <TraceRule style="margin:var(--space-8) 0" />

    <Input v-if="jobs.length > 4" v-model="query" placeholder="Filter by role, company or location…" />

    <p v-if="error" style="font:var(--type-small); color:var(--danger)">{{ error }}</p>

    <div v-if="!loading" style="display:grid; gap:var(--space-4); margin-top:var(--space-6)">
      <Card v-for="j in filtered" :key="j.id" variant="outline">
        <div style="display:flex; justify-content:space-between; gap:var(--space-6); align-items:center; flex-wrap:wrap">
          <div style="display:grid; gap:4px; min-width:0">
            <span style="font:var(--type-h5); color:var(--text-strong)">{{ j.title }}</span>
            <span style="font-family:var(--font-mono); font-size:var(--text-xs); color:var(--text-muted)">
              {{ j.sponsor }}
              <template v-if="j.location"> · {{ j.location }}</template>
              <template v-if="j.closes"> · closes {{ j.closes }}</template>
            </span>
          </div>
          <Button v-if="j.applyUrl" size="sm" :href="j.applyUrl">Apply</Button>
          <Badge v-else tone="neutral">Ask the sponsor</Badge>
        </div>
      </Card>

      <Card v-if="!filtered.length" variant="outline">
        <p style="margin:0; font:var(--type-small); color:var(--text-muted)">
          {{ term ? 'Nothing matches that.' : 'Nothing open at the moment — sponsors post here through the portal.' }}
        </p>
      </Card>
    </div>
  </main>
</template>
