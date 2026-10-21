<script setup>
// Events calendar. Upcoming by default, with past events on request so the
// term's record stays visible after the fact.
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import api from '@/api'
import Card from '@/components/ds/Card.vue'
import Button from '@/components/ds/Button.vue'
import Badge from '@/components/ds/Badge.vue'
import EventCard from '@/components/ds/EventCard.vue'
import NewsletterSignup from '@/components/NewsletterSignup.vue'
import TraceRule from '@/components/ds/TraceRule.vue'
import session from '@/stores/session'

const router = useRouter()
const events = ref([])
const loading = ref(true)
const error = ref('')
const showPast = ref(false)

async function load() {
  // Only show the loading state on the first fetch. Toggling past events
  // re-fetches, and blanking the grid mid-toggle is what made the page jump.
  loading.value = !events.value.length
  error.value = ''
  try {
    const data = await api.get('/events', { upcoming: !showPast.value, limit: 200 })
    events.value = data.events
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

onMounted(load)

function toggle() {
  showPast.value = !showPast.value
  load()
}

const now = Date.now()
const upcoming = computed(() => events.value.filter((e) => new Date(e.startsAt) >= now))
const past = computed(() =>
  events.value.filter((e) => new Date(e.startsAt) < now).reverse(),
)

const heading = computed(() =>
  loading.value
    ? 'Loading the calendar…'
    : upcoming.value.length
      ? `${upcoming.value.length} event${upcoming.value.length === 1 ? '' : 's'} coming up.`
      : 'Nothing on the calendar yet.',
)

function open(event) {
  router.push(`/events/${event.slug}`)
}
</script>

<template>
  <main style="max-width:var(--container-lg); margin:0 auto; padding:var(--space-10) var(--space-6); min-height:70vh">
    <header style="display:grid; gap:var(--space-4)">
      <span style="font-family:var(--font-mono); font-size:var(--text-2xs); letter-spacing:var(--tracking-label); text-transform:uppercase; color:var(--text-faint)">
        What's on
      </span>
      <h1 style="margin:0; font:var(--type-h1)">{{ heading }}</h1>
      <p v-if="session.academicYear" style="margin:0; font:var(--type-small); color:var(--text-muted)">
        Academic year {{ session.academicYear }}. Tickets are bought on the Union
        shop — we match your purchase to your booking automatically.
      </p>
      <!-- A plain anchor to the session-free feed: this is how somebody finds
           the URL to paste into their calendar app, which is exactly the
           client that cannot log in. -->
      <p style="margin:0; font:var(--type-small); color:var(--text-muted)">
        <a
          data-testid="calendar-subscribe-link"
          href="/api/events.ics"
          style="color:inherit"
        >Subscribe to the calendar</a>
        — every published event, straight into your calendar app.
      </p>
    </header>

    <!-- Where people look for events is where they think to ask to hear
         about them. -->
    <NewsletterSignup style="margin-top:var(--space-6)" />

    <TraceRule style="margin:var(--space-8) 0" />

    <p v-if="error" style="font:var(--type-small); color:var(--danger)">{{ error }}</p>

    <div v-if="loading" style="font:var(--type-small); color:var(--text-faint)">Loading…</div>

    <template v-else>
      <div
        v-if="upcoming.length"
        style="display:grid; grid-template-columns:repeat(auto-fill, minmax(min(100%, 300px), 1fr)); gap:var(--space-6)"
      >
        <EventCard
          v-for="e in upcoming"
          :key="e.slug"
          :category="e.category"
          :title="e.title"
          :date="e.date"
          :time="e.time"
          :venue="e.venue"
          :status="e.status"
          :status-tone="e.statusTone"
          @click="open(e)"
        />
      </div>

      <Card v-else variant="outline">
        <p style="margin:0; font:var(--type-small); color:var(--text-muted)">
          No events scheduled yet. Check back after the committee's first meeting of term.
        </p>
      </Card>

      <div style="margin-top:var(--space-10)">
        <Button variant="ghost" @click="toggle">
          {{ showPast ? 'Show upcoming only' : 'Show past events too' }}
        </Button>
      </div>

      <template v-if="showPast && past.length">
        <TraceRule style="margin:var(--space-8) 0" />
        <h2 style="font:var(--type-h3); margin:0 0 var(--space-6)">Already happened</h2>
        <div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(min(100%, 300px), 1fr)); gap:var(--space-6); opacity:.7">
          <EventCard
            v-for="e in past"
            :key="e.slug"
            :category="e.category"
            :title="e.title"
            :date="e.date"
            :time="e.time"
            :venue="e.venue"
            :status="e.status"
            :status-tone="e.statusTone"
            @click="open(e)"
          />
        </div>
      </template>
    </template>
  </main>
</template>
