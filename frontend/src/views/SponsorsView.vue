<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import Card from '@/components/ds/Card.vue'
import Badge from '@/components/ds/Badge.vue'
import Button from '@/components/ds/Button.vue'
import Tag from '@/components/ds/Tag.vue'
import Icon from '@/components/ds/Icon.vue'
import ImageSlot from '@/components/ds/ImageSlot.vue'
import api from '@/api'

const router = useRouter()
const open = ref(null)

// Live sponsors only. The mockup's invented ones (IMC, Arm, Nordic, National
// Grid) are gone — publishing a sponsor EESoc does not have is worse than an
// empty page.
const sponsors = ref([])
const jobs = ref([])
const loading = ref(true)

// Live role counts come from the listings, not the `roles` column, which is a
// number somebody typed in and will drift.
function jobCount(sponsor) {
  const live = jobs.value.filter((j) => j.sponsorSlug === sponsor.id).length
  return live || sponsor.roles || 0
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

function toggle(i) {
  open.value = open.value === i ? null : i
}
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

    <div style="display:grid; gap:var(--space-4)">
      <Card v-for="(s, i) in sponsors" :key="i" variant="outline" :padded="false">
        <a href="#" class="sponsor-row" @click.prevent="toggle(i)">
          <div class="sponsor-row__logo">
            <img
              v-if="s.logoUrl"
              :src="s.logoUrl"
              :alt="s.name"
              class="sponsor-row__img"
            />
            <ImageSlot v-else :placeholder="s.name" shape="rect" />
          </div>
          <div style="display:grid; gap:6px">
            <span style="font-family:var(--font-display); font-weight:700; font-size:var(--text-xl); letter-spacing:var(--tracking-heading); color:var(--text-strong)">{{ s.name }}</span>
            <span style="font:var(--type-small); color:var(--text-muted)">{{ s.oneLine }}</span>
          </div>
          <div style="display:flex; align-items:center; gap:var(--space-4)">
            <!-- Only an admin is ever served a draft, so this never appears for
                 a member. It is the preview events and resource pages give. -->
            <Badge v-if="s.active === false" tone="neutral">Draft</Badge>
            <Badge tone="accent">{{ s.sector }}</Badge>
            <Icon :name="open === i ? 'chevron-up' : 'chevron-down'" :size="20" />
          </div>
        </a>

        <div v-if="open === i" class="sponsor-detail">
          <div style="display:grid; gap:var(--space-5)">
            <p style="margin:0; font:var(--type-body-long); color:var(--text-body); max-width:62ch">{{ s.blurb }}</p>
            <div style="display:grid; gap:var(--space-2)">
              <span class="label-muted">With us this year</span>
              <div style="display:flex; gap:8px; flex-wrap:wrap">
                <Tag v-for="(w, wi) in s.withUs" :key="wi">{{ w }}</Tag>
              </div>
            </div>
          </div>
          <div style="display:grid; gap:var(--space-3); align-content:start">
            <span class="label-muted">Links</span>
            <a :href="`https://${s.site}`" target="_blank" rel="noopener" style="font-family:var(--font-mono); font-size:var(--text-sm)">{{ s.site }}</a>
            <a
              v-if="s.cvContact"
              :href="`mailto:${s.cvContact}?subject=EESoc%20CV`"
              style="font-family:var(--font-mono); font-size:var(--text-sm)"
            >Send a CV{{ s.cvContactName ? ` (${s.cvContactName})` : '' }}</a>
            <span
              v-if="s.cvContactNote"
              style="font:var(--type-2xs); color:var(--text-faint)"
            >{{ s.cvContactNote }}</span>
            <router-link
              to="/jobs"
              style="font-family:var(--font-mono); font-size:var(--text-sm)"
            >Open roles ({{ jobCount(s) }})</router-link>
            <div style="padding-top:var(--space-2)">
              <!-- Straight to the sponsor's own careers page when they have given
                   one; the in-house listings otherwise. A dead "Apply" button is
                   worse than no button. -->
              <Button
                v-if="s.site"
                size="sm"
                block
                :href="s.site.startsWith('http') ? s.site : `https://${s.site}`"
              >Apply</Button>
              <Button v-else size="sm" block variant="secondary" @click="router.push('/jobs')">
                See open roles
              </Button>
            </div>
          </div>
        </div>
      </Card>
    </div>

    <Card variant="offset">
      <div class="sponsor-cta">
        <div style="display:grid; gap:var(--space-3)">
          <h2 style="margin:0; font:var(--type-h2); letter-spacing:var(--tracking-heading); color:var(--text-strong)">Hire the people who build the hardware.</h2>
          <p style="margin:0; font:var(--type-small); color:var(--text-muted); max-width:62ch">
            One package, £4,000 a year, all of it negotiable. Sponsors get the portal, the CV book and a named event.
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
.sponsor-row {
  display: grid;
  grid-template-columns: 140px minmax(0, 1fr) auto;
  gap: var(--space-8);
  align-items: center;
  padding: var(--space-6);
  text-decoration: none;
  color: inherit;
}
.sponsor-row:hover {
  text-decoration: none;
}
.sponsor-row__logo {
  height: 52px;
  background: var(--white);
  border: 1px solid var(--border-hairline);
  border-radius: 4px;
  position: relative;
  overflow: hidden;
}
/* Supplied logos vary wildly in aspect ratio and in how much whitespace the
   brand pack bakes in, so contain them and add our own breathing room rather
   than trusting the file to be cropped sensibly. */
.sponsor-row__img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  padding: 6px;
  box-sizing: border-box;
}
.sponsor-detail {
  border-top: 2px solid var(--border-strong);
  padding: var(--space-8) var(--space-6);
  display: grid;
  grid-template-columns: minmax(0, 1fr) 280px;
  gap: var(--space-10);
  background: var(--surface-sunken);
}
.sponsor-cta {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: var(--space-8);
  align-items: center;
}
</style>
