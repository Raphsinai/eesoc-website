<script setup>
// Resources — a sidebar of pages and a nested body, mirroring what eesoc.com
// publishes (page -> section -> module -> per-year feedback). Every node here
// is a database row an admin can edit.
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import api from '@/api'
import Card from '@/components/ds/Card.vue'
import Badge from '@/components/ds/Badge.vue'
import Button from '@/components/ds/Button.vue'
import Input from '@/components/ds/Input.vue'
import TraceRule from '@/components/ds/TraceRule.vue'
import { renderMarkdown } from '@/utils/markdown'
import { applySeo } from '@/seo'
import session from '@/stores/session'

const route = useRoute()
const router = useRouter()

const pages = ref([])
const page = ref(null)
const loading = ref(true)
const error = ref('')
const query = ref('')
const open = ref(new Set())

const currentSlug = computed(() => route.params.slug || pages.value[0]?.slug || 'resources')

async function loadIndex() {
  try {
    const data = await api.get('/resources')
    pages.value = data.pages
  } catch (e) {
    error.value = e.message
  }
}

async function loadPage(slug) {
  if (!slug) return
  // Deliberately NOT clearing `page` here. Blanking the body collapses the
  // page to a single line, which is what made the sidebar jump and the whole
  // view flash between categories. The old content stays until the new
  // content is ready to replace it.
  loading.value = !page.value
  error.value = ''
  try {
    page.value = await api.get(`/resources/${slug}`)
    // Each resource page gets its own title and description; eesoc.com reuses
    // one description across all of them, so none can rank for anything.
    applySeo({
      title: page.value.title,
      description: page.value.description,
      path: `/resources/${slug}`,
    })
    // Sections start open, deeper nodes collapsed — a module feedback page has
    // ~60 year entries and expanding them all is unreadable.
    open.value = new Set(page.value.entries.map((e) => e.id))
  } catch (e) {
    error.value = e.status === 404 ? 'That page does not exist.' : e.message
    page.value = null
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await loadIndex()
  await loadPage(currentSlug.value)
})

watch(() => route.params.slug, (slug) => loadPage(slug || pages.value[0]?.slug))

function toggle(id) {
  const next = new Set(open.value)
  next.has(id) ? next.delete(id) : next.add(id)
  open.value = next
}

function go(slug) {
  router.push(`/resources/${slug}`)
}

const term = computed(() => query.value.trim().toLowerCase())

/** Filter the tree, keeping a branch if it or any descendant matches. */
function filterTree(nodes) {
  if (!term.value) return nodes
  const walk = (list) =>
    list
      .map((node) => {
        const children = walk(node.children || [])
        const hit =
          node.title.toLowerCase().includes(term.value) ||
          (node.body || '').toLowerCase().includes(term.value)
        return hit || children.length ? { ...node, children } : null
      })
      .filter(Boolean)
  return walk(nodes)
}

const entries = computed(() => filterTree(page.value?.entries || []))
const matchCount = computed(() => {
  const count = (list) => list.reduce((n, e) => n + 1 + count(e.children || []), 0)
  return count(entries.value)
})
</script>

<template>
  <main class="resources-layout">
    <aside class="resources-nav">
      <span class="eyebrow">Resources</span>
      <nav style="display:grid; gap:2px; margin-top:var(--space-4)">
        <button
          v-for="p in pages"
          :key="p.slug"
          class="nav-item"
          :class="{ 'nav-item--active': p.slug === currentSlug }"
          @click="go(p.slug)"
        >
          <span style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap">{{ p.label }}</span>
          <Badge v-if="!p.published" tone="neutral">Draft</Badge>
          <Badge v-else-if="p.membersOnly" tone="brand">Members</Badge>
        </button>
      </nav>

      <TraceRule style="margin:var(--space-6) 0" />
      <Input v-model="query" placeholder="Search this page…" />
      <p v-if="term" style="margin:var(--space-3) 0 0; font:var(--type-2xs); color:var(--text-faint)">
        {{ matchCount }} match{{ matchCount === 1 ? '' : 'es' }}
      </p>

      <router-link
        v-if="session.capabilities.includes('site')"
        to="/admin?tab=resources"
        style="display:block; margin-top:var(--space-6); font:var(--type-2xs); color:var(--text-faint)"
      >
        Edit these pages →
      </router-link>
    </aside>

    <div class="resources-body">
      <p v-if="loading" style="font:var(--type-small); color:var(--text-faint)">Loading…</p>
      <p v-else-if="error" style="font:var(--type-small); color:var(--danger)">{{ error }}</p>

      <template v-else-if="page">
        <h1 style="margin:0; font:var(--type-h1); color:var(--text-strong)">{{ page.title }}</h1>

        <div
          v-if="page.intro"
          class="prose"
          style="margin-top:var(--space-5)"
          v-html="renderMarkdown(page.intro)"
        />

        <Card v-if="page.locked" variant="offset" style="margin-top:var(--space-8)">
          <p style="margin:0; font:var(--type-small)">{{ page.message }}</p>
        </Card>

        <div v-else style="margin-top:var(--space-8); display:grid; gap:var(--space-6)">
          <section v-for="node in entries" :key="node.id">
            <button class="section-head" @click="toggle(node.id)">
              <span>{{ node.title }}</span>
              <span class="section-head__mark">{{ open.has(node.id) ? '–' : '+' }}</span>
            </button>

            <div v-show="open.has(node.id)" style="padding:var(--space-4) 0 0">
              <div v-if="node.body" class="prose" v-html="renderMarkdown(node.body)" />
              <a
                v-if="node.url"
                :href="node.url"
                target="_blank"
                rel="noopener noreferrer"
                class="resource-link"
              >{{ node.title }} ↗</a>

              <div
                v-for="child in node.children"
                :key="child.id"
                style="margin-top:var(--space-5); padding-left:var(--space-5); border-left:2px solid var(--border-hairline)"
              >
                <h3 style="margin:0 0 var(--space-2); font:var(--type-h5); color:var(--text-strong)">
                  {{ child.title }}
                </h3>
                <a
                  v-if="child.url"
                  :href="child.url"
                  target="_blank"
                  rel="noopener noreferrer"
                  class="resource-link"
                >Open ↗</a>
                <div v-if="child.body" class="prose" v-html="renderMarkdown(child.body)" />

                <details v-for="leaf in child.children" :key="leaf.id" style="margin-top:var(--space-3)">
                  <summary class="leaf-summary">{{ leaf.title }}</summary>
                  <div class="prose" style="padding:var(--space-3) 0 0" v-html="renderMarkdown(leaf.body)" />
                </details>
              </div>
            </div>
          </section>

          <p v-if="!entries.length" style="font:var(--type-small); color:var(--text-faint)">
            {{ term ? 'Nothing on this page matches that.' : 'Nothing here yet.' }}
          </p>
        </div>
      </template>
    </div>
  </main>
</template>

<style scoped>
.eyebrow {
  font: var(--type-eyebrow);
  letter-spacing: var(--tracking-eyebrow);
  text-transform: uppercase;
  color: var(--text-accent);
}
.resources-layout {
  max-width: var(--container-xl);
  margin: 0 auto;
  padding: var(--space-16) var(--space-8) var(--space-24);
  display: grid;
  grid-template-columns: 300px minmax(0, 1fr);
  gap: var(--space-12);
  align-items: start;
}
.resources-nav {
  position: sticky;
  top: var(--space-8);
  /* Pinned width. The grid column is already fixed, but the badges ("Draft",
     "Members") vary in width per page and would otherwise stretch the flex
     items and make the column look like it is resizing. */
  width: 100%;
  min-width: 0;
}
.nav-item {
  display: flex;
  min-width: 0;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  width: 100%;
  padding: var(--space-3) var(--space-4);
  background: none;
  border: 0;
  border-left: 2px solid transparent;
  font: var(--type-small);
  color: var(--text-body);
  text-align: left;
  cursor: pointer;
}
.nav-item:hover {
  background: var(--surface-sunken);
}
.nav-item--active {
  border-left-color: var(--text-accent);
  color: var(--text-strong);
  font-weight: 600;
}
.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding: var(--space-3) 0;
  background: none;
  border: 0;
  border-bottom: 1px solid var(--border-hairline);
  font-family: var(--font-display);
  font-weight: 700;
  font-size: var(--text-lg);
  letter-spacing: var(--tracking-heading);
  color: var(--text-strong);
  text-align: left;
  cursor: pointer;
}
.section-head__mark {
  font-family: var(--font-mono);
  color: var(--text-faint);
}
.leaf-summary {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--text-muted);
  cursor: pointer;
}
.resource-link {
  display: inline-block;
  margin: var(--space-2) 0;
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--text-accent);
}
.prose :deep(p) {
  margin: 0 0 var(--space-3);
  font: var(--type-body-long);
  color: var(--text-body);
}
.prose :deep(ul) {
  margin: 0 0 var(--space-3);
  padding-left: var(--space-5);
}
.prose :deep(li) {
  font: var(--type-body-long);
  color: var(--text-body);
}
.prose :deep(a) {
  color: var(--text-accent);
}
.prose :deep(code) {
  font-family: var(--font-mono);
  font-size: 0.9em;
  background: var(--surface-sunken);
  padding: 0 4px;
}
@media (max-width: 900px) {
  .resources-layout {
    grid-template-columns: minmax(0, 1fr);
  }
  .resources-nav {
    position: static;
  }
}
.resources-body {
  min-height: 60vh;
}
</style>
