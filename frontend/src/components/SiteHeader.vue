<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Logo from '@/components/ds/Logo.vue'
import Icon from '@/components/ds/Icon.vue'
import Input from '@/components/ds/Input.vue'
import Button from '@/components/ds/Button.vue'
import api from '@/api'
import { nav } from '@/data/site'
import session, { displayName, logout } from '@/stores/session'

defineEmits(['open-login'])

// The sponsor portal and committee admin are only navigable by the people who
// can actually use them; both are enforced server-side as well.
const visibleNav = computed(() =>
  nav
    .filter((n) => n.id !== 'portal' || session.isSponsor || session.isAdmin)
    .concat(session.isAdmin ? [{ id: 'admin', label: 'Admin', to: '/admin' }] : []),
)

const route = useRoute()
const router = useRouter()

// Search hits the API rather than a client-side index built from site.js —
// that index was mockup data, so it offered events that do not exist and could
// never find a real one.
const q = ref('')
const results = ref([])
const searching = ref(false)
let timer = null
let seq = 0

const query = computed(() => q.value.trim())
const hasQuery = computed(() => query.value.length > 0)
const noResults = computed(
  () => hasQuery.value && !searching.value && results.value.length === 0,
)

watch(q, (value) => {
  clearTimeout(timer)
  if (value.trim().length < 2) {
    results.value = []
    searching.value = false
    return
  }
  searching.value = true
  // Debounced: one request per pause in typing, not one per keystroke.
  timer = setTimeout(async () => {
    const mine = ++seq
    try {
      const data = await api.get('/search', { q: value.trim() })
      // Drop a response that a later keystroke has already superseded,
      // otherwise slow results can overwrite fast ones.
      if (mine === seq) results.value = data.results
    } catch {
      if (mine === seq) results.value = []
    } finally {
      if (mine === seq) searching.value = false
    }
  }, 180)
})

function isActive(n) {
  return route.name === n.id
}

function goResult(r) {
  q.value = ''
  results.value = []
  router.push(r.to)
}
</script>

<template>
  <header class="site-header">
    <div class="site-header__inner">
      <router-link to="/" class="site-header__logo" aria-label="EESoc home">
        <Logo variant="pins" :height="26" />
      </router-link>

      <nav class="site-nav">
        <router-link
          v-for="n in visibleNav"
          :key="n.id"
          :to="n.to"
          class="site-nav__link"
          :class="{ 'is-active': isActive(n) }"
        >{{ n.label }}</router-link>
      </nav>

      <div class="header-search">
        <span class="header-search__icon"><Icon name="search" :size="18" /></span>
        <span style="flex:1; min-width:0">
          <Input v-model="q" placeholder="Search events, resources…" />
        </span>

        <div v-if="hasQuery" class="header-search__results">
          <a
            v-for="(r, i) in results"
            :key="i"
            href="#"
            class="header-search__result"
            @click.prevent="goResult(r)"
          >
            <span>{{ r.label }}</span>
            <span class="header-search__kind">{{ r.kind }}</span>
          </a>
          <div v-if="noResults" class="header-search__empty">Nothing matches that. Try a module code.</div>
        </div>
      </div>

      <template v-if="session.authenticated">
        <span
          class="site-identity"
          :title="session.user?.email"
        >{{ displayName }}<template v-if="session.isMember"> · member</template></span>
        <Button variant="ghost" size="sm" @click="logout">Log out</Button>
      </template>
      <Button v-else variant="secondary" size="sm" @click="$emit('open-login')">Log in</Button>
    </div>
  </header>
</template>

<style scoped>
.site-header {
  position: sticky;
  top: 0;
  z-index: 40;
  background: var(--surface-card);
  border-bottom: 1px solid var(--border-hairline);
}
.site-header__inner {
  max-width: var(--container-xl);
  margin: 0 auto;
  padding: 0 var(--space-8);
  height: 72px;
  display: flex;
  align-items: center;
  gap: var(--space-8);
}
.site-header__logo {
  display: block;
  line-height: 0;
  flex: none;
}
.site-identity {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--text-muted);
  white-space: nowrap;
}
.site-nav {
  display: flex;
  gap: var(--space-6);
  align-items: center;
}
.site-nav__link {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  font-weight: 600;
  letter-spacing: var(--tracking-label);
  text-transform: uppercase;
  text-decoration: none;
  white-space: nowrap;
  color: var(--text-muted);
  padding-bottom: 3px;
  border-bottom: 3px solid transparent;
  transition: color var(--duration-fast) var(--ease-standard);
}
.site-nav__link:hover {
  color: var(--text-strong);
  text-decoration: none;
}
.site-nav__link.is-active {
  color: var(--text-strong);
  border-bottom-color: var(--gold-400);
}
.header-search {
  position: relative;
  margin-left: auto;
  width: 300px;
  display: flex;
  align-items: center;
  gap: var(--space-3);
}
.header-search__icon {
  color: var(--text-faint);
  display: flex;
  flex: none;
}
.header-search__results {
  position: absolute;
  top: 52px;
  right: 0;
  width: 360px;
  background: var(--surface-card);
  border: 2px solid var(--border-strong);
  border-radius: 8px;
  box-shadow: var(--shadow-lg);
  overflow: hidden;
  z-index: 50;
}
.header-search__result {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: 12px 14px;
  border-bottom: 1px solid var(--border-hairline);
  text-decoration: none;
  color: var(--text-strong);
  font: var(--type-small);
}
.header-search__result:hover {
  background: var(--surface-brand-subtle);
  text-decoration: none;
}
.header-search__kind {
  font: var(--type-eyebrow);
  letter-spacing: var(--tracking-label);
  text-transform: uppercase;
  color: var(--text-accent);
  white-space: nowrap;
}
.header-search__empty {
  padding: 14px;
  font: var(--type-small);
  color: var(--text-muted);
}
</style>
