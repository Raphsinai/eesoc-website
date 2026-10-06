<script setup>
// Email preferences, and the page an unsubscribe link lands on.
//
// One view for both because they are the same question asked from two places.
// Somebody arriving from a link in a message has no session and must still be
// able to act — so the unsubscribe half works entirely on the token in the URL,
// and the preferences half only appears once we know who they are.
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import api from '@/api'
import Card from '@/components/ds/Card.vue'
import Button from '@/components/ds/Button.vue'
import Badge from '@/components/ds/Badge.vue'
import TraceRule from '@/components/ds/TraceRule.vue'
import session from '@/stores/session'

const route = useRoute()

const loading = ref(true)
const busy = ref(false)
const error = ref('')
const notice = ref('')

// --- the unsubscribe half ---------------------------------------------------
const target = ref(null)      // what the link in the email is about
const done = ref(false)

// --- the preferences half ---------------------------------------------------
const topics = ref([])
const status = ref(null)
const alerts = ref([])

const isUnsubscribeLink = computed(
  () => Boolean(route.query.alert || (route.query.e && route.query.t)),
)

/** Per-category topics are grouped separately: there can be a lot of them, and
 *  they are a refinement of "new events" rather than a peer of it. */
const general = computed(() => topics.value.filter((t) => !t.category))
const byCategory = computed(() => topics.value.filter((t) => t.category))

async function load() {
  loading.value = true
  error.value = ''
  try {
    if (isUnsubscribeLink.value) {
      try {
        target.value = await api.get('/email/unsubscribe', {
          e: route.query.e,
          t: route.query.t,
          topic: route.query.topic,
          alert: route.query.alert,
        })
        done.value = Boolean(target.value?.alreadyDone)
      } catch (e) {
        error.value = e.message
      }
    }

    const data = await api.get('/email/topics')
    topics.value = data.topics

    if (session.authenticated) {
      const [s, a] = await Promise.all([
        api.get('/me/email-status'),
        api.get('/me/sale-alerts'),
      ])
      status.value = s
      alerts.value = a.alerts
    }
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

onMounted(load)

async function confirmUnsubscribe() {
  busy.value = true
  error.value = ''
  try {
    const res = await api.post('/email/unsubscribe', null, {
      e: route.query.e,
      t: route.query.t,
      topic: route.query.topic,
      alert: route.query.alert,
    })
    notice.value = res.message
    done.value = true
    if (session.authenticated) await load()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

/** Saved immediately on toggle. A preferences screen with a Save button is one
 *  people leave without pressing. */
async function toggle(topic) {
  const next = !topic.subscribed
  topic.subscribed = next
  topic.isDefault = false
  busy.value = true
  error.value = ''
  try {
    await api.put('/me/email-preferences', {
      preferences: { [topic.key]: next },
    })
    notice.value = next
      ? `You'll get ${topic.label.toLowerCase()}.`
      : `You won't get ${topic.label.toLowerCase()} any more.`
    if (next && status.value?.unsubscribedFromAll) await load()
  } catch (e) {
    topic.subscribed = !next
    error.value = e.message
  } finally {
    busy.value = false
  }
}

async function resubscribe() {
  busy.value = true
  try {
    const res = await api.post('/me/email-resubscribe')
    notice.value = res.message
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

async function dropAlert(alert) {
  busy.value = true
  try {
    await api.del(`/events/${alert.eventSlug}/notify-me`)
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="email-page">
    <h1 style="margin:0; font:var(--type-h1)">Email</h1>

    <p v-if="loading" style="font:var(--type-small); color:var(--text-faint)">Loading…</p>

    <template v-else>
      <!-- ===================== unsubscribe confirmation ===================== -->
      <Card v-if="isUnsubscribeLink && target" variant="offset">
        <template v-if="done">
          <div style="display:flex; align-items:center; gap:var(--space-3); flex-wrap:wrap">
            <Badge tone="success">Done</Badge>
            <h2 style="margin:0; font:var(--type-h4)">You're unsubscribed</h2>
          </div>
          <p style="margin:var(--space-4) 0 0; font:var(--type-small); color:var(--text-muted)">
            {{ notice || 'Nothing more will be sent to this address.' }}
          </p>
          <p style="margin:var(--space-3) 0 0; font:var(--type-2xs); color:var(--text-faint)">
            Changed your mind? Sign in and turn the topics below back on.
          </p>
        </template>

        <template v-else>
          <h2 style="margin:0 0 var(--space-3); font:var(--type-h4)">
            {{
              target.kind === 'alert'
                ? `Stop alerts about ${target.eventTitle}?`
                : target.kind === 'topic'
                  ? `Unsubscribe from ${target.topicLabel}?`
                  : 'Unsubscribe from EESoc email?'
            }}
          </h2>
          <p style="margin:0 0 var(--space-4); font:var(--type-small); color:var(--text-muted)">
            For <strong>{{ target.email }}</strong>.
            <template v-if="target.kind === 'all'">
              You'll stop getting newsletters and announcements. Email about
              something you've actually booked — your ticket, your sign-in code —
              still comes through.
            </template>
          </p>
          <Button :disabled="busy" @click="confirmUnsubscribe">
            {{ busy ? 'Working…' : 'Yes, unsubscribe' }}
          </Button>
        </template>
      </Card>

      <p v-if="error" style="font:var(--type-small); color:var(--danger)">{{ error }}</p>

      <!-- ========================== preferences ========================== -->
      <Card v-if="!session.authenticated" variant="outline">
        <h2 style="margin:0 0 var(--space-3); font:var(--type-h4)">Manage what you get</h2>
        <p style="margin:0; font:var(--type-small); color:var(--text-muted)">
          Sign in to choose which EESoc email you'd like. You don't need an
          account to unsubscribe — the link in any message is enough.
        </p>
      </Card>

      <template v-else>
        <Card v-if="status?.unsubscribedFromAll" variant="outline">
          <div style="display:flex; align-items:center; gap:var(--space-3); flex-wrap:wrap">
            <Badge tone="danger">Unsubscribed</Badge>
            <span style="font:var(--type-small)">
              This address is opted out of everything, so nothing below is sent.
            </span>
            <Button size="sm" variant="secondary" :disabled="busy" @click="resubscribe">
              Start getting email again
            </Button>
          </div>
        </Card>

        <Card variant="offset">
          <h2 style="margin:0 0 var(--space-2); font:var(--type-h4)">What you get</h2>
          <p style="margin:0 0 var(--space-5); font:var(--type-small); color:var(--text-muted)">
            Changes save as you make them. {{ status?.email }}
          </p>

          <label v-for="t in general" :key="t.key" class="topic">
            <input type="checkbox" :checked="t.subscribed" :disabled="busy" @change="toggle(t)" />
            <span>
              <strong class="topic__label">{{ t.label }}</strong>
              <span class="topic__desc">{{ t.description }}</span>
            </span>
          </label>

          <template v-if="byCategory.length">
            <TraceRule style="margin:var(--space-6) 0 var(--space-5)" />
            <h3 style="margin:0 0 var(--space-2); font:var(--type-h5)">Only certain events</h3>
            <p style="margin:0 0 var(--space-4); font:var(--type-2xs); color:var(--text-faint)">
              Narrower than "new events" above — useful if you only want the
              careers programme, say.
            </p>
            <label v-for="t in byCategory" :key="t.key" class="topic">
              <input type="checkbox" :checked="t.subscribed" :disabled="busy" @change="toggle(t)" />
              <span>
                <strong class="topic__label">{{ t.label }}</strong>
                <span class="topic__desc">{{ t.description }}</span>
              </span>
            </label>
          </template>
        </Card>

        <Card v-if="alerts.length" variant="outline">
          <h2 style="margin:0 0 var(--space-4); font:var(--type-h4)">Events you're watching</h2>
          <div
            v-for="a in alerts"
            :key="a.id"
            style="display:flex; align-items:center; justify-content:space-between; gap:var(--space-3); padding:var(--space-3) 0; border-top:1px solid var(--border-faint)"
          >
            <span style="font:var(--type-small)">
              <router-link :to="`/events/${a.eventSlug}`" style="color:inherit">{{ a.eventTitle }}</router-link>
              <Badge v-if="a.notified" tone="neutral" style="margin-left:var(--space-2)">told you</Badge>
            </span>
            <Button size="sm" variant="ghost" :disabled="busy" @click="dropAlert(a)">Stop</Button>
          </div>
        </Card>
      </template>

      <p v-if="notice && !isUnsubscribeLink" style="font:var(--type-small); color:var(--text-muted)">
        {{ notice }}
      </p>
    </template>
  </main>
</template>

<style scoped>
.email-page {
  max-width: var(--container-sm, 720px);
  margin: 0 auto;
  padding: var(--space-10) var(--space-6);
  display: grid;
  gap: var(--space-6);
}

.topic {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: var(--space-3);
  align-items: start;
  padding: var(--space-3) 0;
  border-top: 1px solid var(--border-faint);
  cursor: pointer;
}

/* The first row sits directly under the intro text, which is its own
   separator. */
.topic:first-of-type {
  border-top: 0;
}

.topic input {
  margin-top: 3px;
  width: 16px;
  height: 16px;
  accent-color: var(--action-primary);
}

.topic__label {
  display: block;
  font: var(--type-small);
  color: var(--text-strong);
}

.topic__desc {
  display: block;
  margin-top: 2px;
  font: var(--type-2xs);
  color: var(--text-muted);
}
</style>
