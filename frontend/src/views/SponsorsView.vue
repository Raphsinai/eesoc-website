<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import Card from '@/components/ds/Card.vue'
import Badge from '@/components/ds/Badge.vue'
import Button from '@/components/ds/Button.vue'
import Dialog from '@/components/ds/Dialog.vue'
import Tag from '@/components/ds/Tag.vue'
import ImageSlot from '@/components/ds/ImageSlot.vue'
import api from '@/api'

const router = useRouter()

// Live sponsors only. The mockup's invented ones (IMC, Arm, Nordic, National
// Grid) are gone — publishing a sponsor EESoc does not have is worse than an
// empty page.
const sponsors = ref([])
const jobs = ref([])
const loading = ref(true)

// Index of the card showing its full detail, or null. Held as an index rather
// than a copy of the row, so a refresh behind an open card cannot leave stale
// content on screen.
const open = ref(null)
const current = computed(() =>
  open.value === null ? null : (sponsors.value[open.value] ?? null),
)

// Live role counts come from the listings, not the `roles` column, which is a
// number somebody typed in and will drift.
function jobCount(sponsor) {
  const live = jobs.value.filter((j) => j.sponsorSlug === sponsor.id).length
  return live || sponsor.roles || 0
}

function siteUrl(site) {
  return site?.startsWith('http') ? site : `https://${site}`
}

function contactHref(c) {
  return c.kind === 'email' ? `mailto:${c.value}?subject=EESoc` : c.value
}

onMounted(async () => {
  try {
    const [data, jobData] = await Promise.all([
      api.get('/sponsors'),
      api.get('/jobs').catch(() => ({ jobs: [] })),
    ])
    sponsors.value = data.sponsors
    jobs.value = jobData.jobs
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <main class="sponsors-page">
    <div style="display:grid; gap:var(--space-4)">
      <span class="eyebrow">Sponsors 2026–27</span>
      <h1 style="margin:0; font:var(--type-h1); letter-spacing:var(--tracking-display); color:var(--text-strong)">Who backs us, and why it matters to you.</h1>
      <p style="margin:0; font:var(--type-body); color:var(--text-body); max-width:62ch">
        Every event you attend is paid for by one of these firms. Open a card for their open roles and the events they run with us.
      </p>
      <p
        v-if="sponsors.some((s) => s.active === false)"
        style="margin:0; font:var(--type-small); color:var(--text-muted)"
      >
        Cards marked <strong>Draft</strong> are visible to the committee only.
      </p>
    </div>

    <div class="sponsor-grid">
      <Card
        v-for="(s, i) in sponsors"
        :key="s.id ?? i"
        as="button"
        type="button"
        variant="outline"
        interactive
        :padded="false"
        class="sponsor-card"
        :aria-label="`${s.name} — open details`"
        @click="open = i"
      >
        <div class="sponsor-card__logo">
          <img v-if="s.logoUrl" :src="s.logoUrl" :alt="s.name" class="sponsor-card__img" />
          <ImageSlot v-else :placeholder="s.name" shape="rect" />
        </div>
        <div class="sponsor-card__body">
          <div class="sponsor-card__badges">
            <!-- Only an admin is ever served a draft, so this never appears for
                 a member. It is the preview events and resource pages give. -->
            <Badge v-if="s.active === false" tone="neutral">Draft</Badge>
            <Badge tone="accent">{{ s.sector }}</Badge>
          </div>
          <span class="sponsor-card__name">{{ s.name }}</span>
          <span class="sponsor-card__line">{{ s.oneLine }}</span>
        </div>
      </Card>
    </div>

    <!-- The expanded card. Dialog already gives the darkened scrim, Escape to
         close and the body scroll lock; the width is the near-full-screen part. -->
    <Dialog
      :open="current !== null"
      :title="current?.name || ''"
      width="min(1040px, 94vw)"
      @close="open = null"
    >
      <div v-if="current" class="sponsor-detail">
        <div class="sponsor-detail__main">
          <div class="sponsor-detail__head">
            <div class="sponsor-detail__logo">
              <img
                v-if="current.logoUrl"
                :src="current.logoUrl"
                :alt="current.name"
                class="sponsor-detail__img"
              />
              <ImageSlot v-else :placeholder="current.name" shape="rect" />
            </div>
            <div class="sponsor-detail__heading">
              <div class="sponsor-card__badges">
                <Badge v-if="current.active === false" tone="neutral">Draft</Badge>
                <Badge tone="accent">{{ current.sector }}</Badge>
              </div>
              <span class="sponsor-detail__line">{{ current.oneLine }}</span>
            </div>
          </div>

          <p
            v-if="current.blurb"
            style="margin:0; font:var(--type-body-long); color:var(--text-body); max-width:62ch"
          >{{ current.blurb }}</p>

          <div v-if="current.withUs?.length" style="display:grid; gap:var(--space-2)">
            <span class="label-muted">With us this year</span>
            <div style="display:flex; gap:8px; flex-wrap:wrap">
              <Tag v-for="(w, wi) in current.withUs" :key="wi">{{ w }}</Tag>
            </div>
          </div>

          <div v-if="current.events?.length" style="display:grid; gap:var(--space-2)">
            <span class="label-muted">Events with us</span>
            <router-link
              v-for="ev in current.events"
              :key="ev.slug"
              :to="`/events/${ev.slug}`"
              style="font-family:var(--font-mono); font-size:var(--text-sm)"
            >{{ ev.title }}<span style="color:var(--text-faint)"> · {{ ev.date }}</span></router-link>
          </div>
        </div>

        <div class="sponsor-detail__side">
          <span class="label-muted">Links</span>
          <a
            v-if="current.site"
            :href="siteUrl(current.site)"
            target="_blank"
            rel="noopener"
            style="font-family:var(--font-mono); font-size:var(--text-sm)"
          >{{ current.site }}</a>
          <!-- The sponsor's own contact points: CV addresses, careers pages,
               booking links. Members write to them directly; EESoc never
               handles a CV. -->
          <div v-for="(c, ci) in current.contacts" :key="ci" style="display:grid; gap:2px">
            <a
              :href="contactHref(c)"
              :target="c.kind === 'link' ? '_blank' : undefined"
              :rel="c.kind === 'link' ? 'noopener' : undefined"
              style="font-family:var(--font-mono); font-size:var(--text-sm)"
            >{{ c.label || c.value }}</a>
            <span v-if="c.label && c.kind === 'email'" style="font:var(--type-2xs); color:var(--text-muted)">{{ c.value }}</span>
            <span v-if="c.note" style="font:var(--type-2xs); color:var(--text-faint)">{{ c.note }}</span>
          </div>
          <router-link
            to="/jobs"
            style="font-family:var(--font-mono); font-size:var(--text-sm)"
          >Open roles ({{ jobCount(current) }})</router-link>
          <div style="padding-top:var(--space-2)">
            <!-- Straight to the sponsor's own careers page when they have given
                 one; the in-house listings otherwise. A dead "Apply" button is
                 worse than no button. -->
            <Button v-if="current.site" size="sm" block :href="siteUrl(current.site)">Apply</Button>
            <Button v-else size="sm" block variant="secondary" @click="router.push('/jobs')">
              See open roles
            </Button>
          </div>
        </div>
      </div>
    </Dialog>

    <Card variant="offset">
      <div class="sponsor-cta">
        <div style="display:grid; gap:var(--space-3)">
          <h2 style="margin:0; font:var(--type-h2); letter-spacing:var(--tracking-heading); color:var(--text-strong)">Hire the people who build the hardware.</h2>
          <p style="margin:0; font:var(--type-small); color:var(--text-muted); max-width:62ch">
            One package, £4,000 a year, all of it negotiable. Sponsors get a workshop, a named event, a careers fair stall and the portal.
          </p>
        </div>
        <Button variant="accent" size="lg" @click="router.push('/portal')">Sponsor us</Button>
      </div>
    </Card>
  </main>
</template>

<style scoped>
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
.sponsors-page {
  max-width: var(--container-lg);
  margin: 0 auto;
  padding: var(--space-16) var(--space-8) var(--space-24);
  display: grid;
  gap: var(--space-12);
}

/* auto-fill, not auto-fit: with three sponsors left on the last row the cards
   stay card-sized instead of stretching across the full width. */
.sponsor-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: var(--space-5);
}

/* A real <button>, so a card is reachable by keyboard and announced as the
   control it is — which means undoing what the UA stylesheet does to one. */
.sponsor-card {
  display: grid;
  grid-template-rows: auto 1fr;
  align-content: start;
  width: 100%;
  margin: 0;
  font: inherit;
  color: inherit;
  text-align: left;
  cursor: pointer;
  overflow: hidden;
}
/* A plain block, deliberately not a centred grid: a grid area gives a replaced
   child no definite height to resolve `height:100%` against, so the image sizes
   itself from its own aspect ratio and overflows a wide logo out of the box.
   object-fit does the centring instead.
   `position:relative` is what keeps ImageSlot — absolute, inset:0 — inside the
   card rather than loose over the whole grid. */
.sponsor-card__logo {
  height: 104px;
  position: relative;
  overflow: hidden;
  background: var(--white);
  border-bottom: 1px solid var(--border-hairline);
}
/* Supplied logos vary wildly in aspect ratio and in how much whitespace the
   brand pack bakes in, so contain them rather than trusting the file to be
   cropped sensibly. */
.sponsor-card__img {
  display: block;
  width: 100%;
  height: 100%;
  padding: var(--space-5);
  box-sizing: border-box;
  object-fit: contain;
}
.sponsor-card__body {
  padding: var(--space-5);
  display: grid;
  gap: var(--space-2);
  align-content: start;
}
.sponsor-card__badges {
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
}
.sponsor-card__name {
  font-family: var(--font-display);
  font-weight: 700;
  font-size: var(--text-lg);
  letter-spacing: var(--tracking-heading);
  color: var(--text-strong);
}
.sponsor-card__line {
  font: var(--type-small);
  color: var(--text-muted);
}

.sponsor-detail {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 280px;
  gap: var(--space-10);
  /* The panel should read as a full-screen expansion of the card whatever the
     sponsor has written, rather than collapsing to a strip while the copy is
     still a placeholder. Dialog caps the total at the viewport. */
  min-height: 68vh;
}
.sponsor-detail__main {
  display: grid;
  gap: var(--space-6);
  align-content: start;
}
.sponsor-detail__side {
  display: grid;
  gap: var(--space-3);
  align-content: start;
}
.sponsor-detail__head {
  display: grid;
  grid-template-columns: 220px minmax(0, 1fr);
  gap: var(--space-6);
  align-items: center;
}
.sponsor-detail__logo {
  height: 112px;
  position: relative;
  overflow: hidden;
  background: var(--white);
  border: 1px solid var(--border-hairline);
  border-radius: 4px;
}
.sponsor-detail__img {
  display: block;
  width: 100%;
  height: 100%;
  padding: var(--space-4);
  box-sizing: border-box;
  object-fit: contain;
}
.sponsor-detail__heading {
  display: grid;
  gap: var(--space-2);
}
.sponsor-detail__line {
  font: var(--type-body);
  color: var(--text-body);
}

.sponsor-cta {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: var(--space-8);
  align-items: center;
}

@media (max-width: 860px) {
  .sponsor-detail,
  .sponsor-cta {
    grid-template-columns: minmax(0, 1fr);
    gap: var(--space-8);
  }
}
@media (max-width: 560px) {
  .sponsors-page {
    padding: var(--space-12) var(--space-5) var(--space-16);
  }
  .sponsor-grid {
    grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
    gap: var(--space-4);
  }
  .sponsor-detail__head {
    grid-template-columns: minmax(0, 1fr);
    gap: var(--space-4);
  }
}
</style>
