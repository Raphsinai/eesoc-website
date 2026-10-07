<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import Button from '@/components/ds/Button.vue'
import Card from '@/components/ds/Card.vue'
import Icon from '@/components/ds/Icon.vue'
import IconButton from '@/components/ds/IconButton.vue'
import EventCard from '@/components/ds/EventCard.vue'
import NewsletterSignup from '@/components/NewsletterSignup.vue'
import ChipFrame from '@/components/ds/ChipFrame.vue'
import TraceRule from '@/components/ds/TraceRule.vue'
import ImageSlot from '@/components/ds/ImageSlot.vue'
import api from '@/api'

const router = useRouter()

const events = ref([])
const committee = ref([])
const committeeRows = ref([])
const committeeIntro = ref('')
const committeeAcademicYear = ref('')
const sponsors = ref([])

onMounted(async () => {
  // Independent sections, so one failing endpoint should not blank the page —
  // each falls back to an empty list and the template renders around it.
  const [ev, cm, sp] = await Promise.allSettled([
    api.get('/events', { upcoming: true, limit: 12 }),
    api.get('/committee'),
    api.get('/sponsors'),
  ])
  if (ev.status === 'fulfilled') events.value = ev.value.events
  if (cm.status === 'fulfilled') {
    committee.value = cm.value.committee
    committeeRows.value = cm.value.rows || []
    committeeIntro.value = cm.value.intro
    committeeAcademicYear.value = cm.value.academicYear
  }
  if (sp.status === 'fulfilled') sponsors.value = sp.value.sponsors
})

// The hero chip reads off the next event, which does not exist until the fetch
// lands (or at all, out of term).
const nextEvent = computed(() => events.value[0] ?? null)
const nextDay = computed(() => nextEvent.value?.date?.split(' ')[1] ?? '—')
const nextLabel = computed(() =>
  nextEvent.value
    ? `${nextEvent.value.date.split(' ')[2]} · ${nextEvent.value.title}`
    : 'Nothing scheduled yet',
)
const homeEvents = computed(() => events.value.slice(0, 3))
const committeeYear = computed(() =>
  committee.value.length && committeeAcademicYear.value
    ? `20${committeeAcademicYear.value.replace('-', '–20')}`
    : '',
)
// Every card is sized off the widest row, so a row of two sits centred at the
// same width as the cards in a row of three rather than stretching to fill.
const committeeColumns = computed(() =>
  Math.min(6, committeeRows.value.reduce((n, g) => Math.max(n, g.members.length), 1)),
)
const committeeHeading = computed(() =>
  committee.value.length
    ? `${committee.value.length} people, one department.`
    : 'Committee list is syncing.',
)
const eventCountLabel = computed(() =>
  events.value.length === 1 ? '1 event this term' : `${events.value.length} events this term`,
)

const track = ref(null)
function scrollBy(dir) {
  if (track.value) track.value.scrollBy({ left: dir * 568, behavior: 'smooth' })
}
</script>

<template>
  <main>
    <!-- Hero -->
    <section class="hero">
      <div class="hero__grid-bg"></div>
      <div class="hero__inner hero-grid">
        <div style="display:grid; gap:var(--space-6)">
          <span class="eyebrow">Autumn / Spring 2026–27</span>
          <h1 style="margin:0; font:var(--type-display); letter-spacing:var(--tracking-display); color:var(--text-strong); text-wrap:pretty">Build things<br />that work.</h1>
          <p style="margin:0; font:var(--type-body); color:var(--text-body); max-width:52ch">
            We run the technical and social calendar for EEE and EIE at Imperial — talks, hardware nights, the careers programme, lockers, and the annual dinner. Log in with your college account to book.
          </p>
          <div style="display:flex; gap:var(--space-3); flex-wrap:wrap">
            <Button size="lg" @click="router.push('/resources')">Browse resources</Button>
            <Button size="lg" variant="secondary" @click="router.push('/lockers')">Book a locker</Button>
          </div>
        </div>
        <div style="display:grid; justify-items:center">
          <ChipFrame padding="var(--space-8) var(--space-10)">
            <div style="display:grid; gap:6px; justify-items:center; text-align:center">
              <span style="font:var(--type-display); font-size:var(--text-4xl); color:var(--text-strong)">{{ nextDay }}</span>
              <span class="eyebrow" style="color:var(--text-muted)">{{ nextLabel }}</span>
            </div>
          </ChipFrame>
        </div>
      </div>
    </section>

    <!-- Upcoming events -->
    <section class="section-x" style="padding-top:var(--section-y)">
      <div style="display:flex; align-items:baseline; justify-content:space-between; gap:var(--space-8); margin-bottom:var(--space-8)">
        <h2 style="margin:0; font:var(--type-h2); letter-spacing:var(--tracking-heading); color:var(--text-strong)">Upcoming events</h2>
        <span class="label-muted">{{ eventCountLabel }}</span>
      </div>
      <div class="grid-3" style="display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:var(--space-6)">
        <EventCard
          v-for="e in homeEvents"
          :key="e.id"
          :category="e.category"
          :title="e.title"
          :date="e.date"
          :time="e.time"
          :venue="e.venue"
          :status="e.status"
          :status-tone="e.statusTone"
          @click="router.push(`/events/${e.slug}`)"
        />
      </div>
      <div v-if="!homeEvents.length" style="font:var(--type-small); color:var(--text-faint)">
        Nothing scheduled yet — the calendar goes up once the committee has met.
      </div>
      <div style="margin-top:var(--space-6)">
        <Button variant="secondary" @click="router.push('/events')">See the whole calendar</Button>
      </div>
      <NewsletterSignup style="margin-top:var(--space-8)" />
      <div style="padding:var(--space-12) 0"><TraceRule /></div>
    </section>

    <!-- Committee -->
    <section class="section-x">
      <div style="display:grid; gap:var(--space-3); margin-bottom:var(--space-8)">
        <span class="eyebrow">Committee {{ committeeYear }}</span>
        <h2 style="margin:0; font:var(--type-h2); letter-spacing:var(--tracking-heading); color:var(--text-strong)">{{ committeeHeading }}</h2>
        <p style="margin:0; font:var(--type-small); color:var(--text-muted); max-width:62ch">{{ committeeIntro }}</p>
      </div>
      <!-- Laid out in the rows the committee chose in the admin dashboard, so
           the exec sits together rather than reflowing wherever the column
           count happens to break. -->
      <div class="committee-grid" :data-cols="committeeColumns">
        <div
          v-for="group in committeeRows"
          :key="group.row"
          class="committee-row"
        >
          <Card v-for="m in group.members" :key="m.cid" variant="default" :padded="false">
            <div style="aspect-ratio:1/1; background:var(--surface-sunken); border-bottom:1px solid var(--border-hairline); position:relative">
              <img
                v-if="m.photoUrl"
                :src="m.photoUrl"
                :alt="m.name"
                style="width:100%; height:100%; object-fit:cover"
              />
              <ImageSlot v-else :placeholder="m.name" shape="rect" />
            </div>
            <div style="padding:var(--space-5) var(--space-5) var(--space-6); display:grid; gap:6px">
              <span class="eyebrow" style="letter-spacing:var(--tracking-label)">{{ m.role }}</span>
              <span style="font-family:var(--font-display); font-weight:700; font-size:var(--text-md); letter-spacing:var(--tracking-heading); color:var(--text-strong)">{{ m.name }}</span>
              <p v-if="m.intro" style="margin:var(--space-2) 0 0; font:var(--type-2xs); color:var(--text-muted)">{{ m.intro }}</p>
            </div>
          </Card>
        </div>
      </div>
    </section>

    <!-- Sponsors carousel -->
    <section class="eesoc-dark sponsors-band">
      <div class="section-x sponsors-band__head">
        <div style="display:grid; gap:var(--space-3)">
          <span class="eyebrow">Our sponsors</span>
          <h2 style="margin:0; font:var(--type-h2); letter-spacing:var(--tracking-heading); color:var(--text-strong)">The firms that keep our uptime.</h2>
        </div>
        <div style="display:flex; gap:var(--space-2); flex:none">
          <IconButton variant="secondary" label="Previous sponsors" @click="scrollBy(-1)"><Icon name="chevron-left" :size="18" /></IconButton>
          <IconButton variant="secondary" label="Next sponsors" @click="scrollBy(1)"><Icon name="chevron-right" :size="18" /></IconButton>
        </div>
      </div>
      <div ref="track" class="sponsors-track">
        <div v-for="(s, i) in sponsors" :key="i" class="sponsor-chip">
          <div style="height:56px; background:var(--white); border-radius:4px; position:relative; overflow:hidden">
            <img
              v-if="s.logoUrl"
              :src="s.logoUrl"
              :alt="s.name"
              style="width:100%; height:100%; object-fit:contain"
            />
            <ImageSlot v-else :placeholder="s.name" shape="rect" />
          </div>
          <div style="display:grid; gap:4px">
            <span style="font-family:var(--font-display); font-weight:700; font-size:var(--text-base); color:var(--text-strong)">{{ s.name }}</span>
            <span style="font-family:var(--font-mono); font-size:var(--text-xs); color:var(--text-muted)">{{ s.strand }}</span>
          </div>
        </div>
      </div>
      <div class="section-x">
        <Button variant="accent" @click="router.push('/sponsors')">See all sponsors</Button>
      </div>
    </section>
  </main>
</template>

<style scoped>
.committee-grid {
  width: 75%;
  margin: 0 auto;
}
.committee-row {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: var(--space-6);
  margin-bottom: var(--space-6);
}
/* Every card is --card-w wide, set once per grid off the widest row, so a short
   row centres at the same card size instead of stretching to fill. */
.committee-row > * {
  flex: 0 0 auto;
  width: var(--card-w, 100%);
}
.committee-grid[data-cols='1'] { --card-w: 100%; }
.committee-grid[data-cols='2'] { --card-w: calc((100% - 1 * var(--space-6)) / 2); }
.committee-grid[data-cols='3'] { --card-w: calc((100% - 2 * var(--space-6)) / 3); }
.committee-grid[data-cols='4'] { --card-w: calc((100% - 3 * var(--space-6)) / 4); }
.committee-grid[data-cols='5'] { --card-w: calc((100% - 4 * var(--space-6)) / 5); }
.committee-grid[data-cols='6'] { --card-w: calc((100% - 5 * var(--space-6)) / 6); }
/* Past three across, cards get too narrow to read, so cap the columns and let
   the extra members wrap. */
@media (max-width: 1200px) {
  .committee-grid {
    width: 90%;
  }
  .committee-grid[data-cols='4'],
  .committee-grid[data-cols='5'],
  .committee-grid[data-cols='6'] {
    --card-w: calc((100% - 2 * var(--space-6)) / 3);
  }
}
@media (max-width: 900px) {
  .committee-grid[data-cols='3'],
  .committee-grid[data-cols='4'],
  .committee-grid[data-cols='5'],
  .committee-grid[data-cols='6'] {
    --card-w: calc((100% - 1 * var(--space-6)) / 2);
  }
}
@media (max-width: 720px) {
  .committee-grid {
    width: 100%;
  }
  .committee-grid[data-cols] {
    --card-w: 100%;
  }
}
.eyebrow {
  font: var(--type-eyebrow);
  letter-spacing: var(--tracking-eyebrow);
  text-transform: uppercase;
  color: var(--text-accent);
}
.label-muted {
  font: var(--type-eyebrow);
  letter-spacing: var(--tracking-label);
  text-transform: uppercase;
  color: var(--text-muted);
}
.section-x {
  max-width: var(--container-xl);
  margin: 0 auto;
  padding: 0 var(--space-8);
}
.hero {
  position: relative;
  overflow: hidden;
  background: var(--surface-card);
  border-bottom: 1px solid var(--border-hairline);
}
.hero__grid-bg {
  position: absolute;
  inset: 0;
  background: var(--bg-grid);
  opacity: 0.9;
}
.hero__inner {
  position: relative;
  max-width: var(--container-xl);
  margin: 0 auto;
  padding: var(--space-24) var(--space-8);
  display: grid;
  grid-template-columns: 1.3fr 0.7fr;
  gap: var(--space-16);
  align-items: center;
}
.sponsors-band {
  margin-top: var(--section-y);
  background: var(--surface-page);
  color: var(--text-body);
  padding: var(--space-16) 0;
}
.sponsors-band__head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--space-8);
  margin-bottom: var(--space-8);
}
.sponsors-track {
  display: flex;
  gap: var(--space-6);
  overflow-x: auto;
  scroll-snap-type: x mandatory;
  padding: 4px var(--space-8) var(--space-6);
}
.sponsor-chip {
  flex: 0 0 260px;
  scroll-snap-align: start;
  border: 2px solid var(--border-default);
  border-radius: 8px;
  padding: var(--space-6);
  display: grid;
  gap: var(--space-4);
  background: var(--surface-card);
}
</style>
