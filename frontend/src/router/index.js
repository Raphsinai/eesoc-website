import { createRouter, createWebHistory } from 'vue-router'

import HomeView from '@/views/HomeView.vue'
import ResourcesView from '@/views/ResourcesView.vue'
import SponsorsView from '@/views/SponsorsView.vue'
import LockersView from '@/views/LockersView.vue'
import PortalView from '@/views/PortalView.vue'
import EventsView from '@/views/EventsView.vue'
import EventView from '@/views/EventView.vue'
import AdminView from '@/views/AdminView.vue'
import AdminEventView from '@/views/AdminEventView.vue'
import JobsView from '@/views/JobsView.vue'
import EmailView from '@/views/EmailView.vue'
import SubscribeView from '@/views/SubscribeView.vue'
import session, { hasAdminArea, refresh } from '@/stores/session'
import { applySeo } from '@/seo'

const routes = [
  // `title` is the page half of eesoc.com's "<Page> | EESoc - Imperial College
  // London" pattern; null means use the bare site name. Views that load their
  // own content (an event, a resource page) refine this once it arrives.
  { path: '/', name: 'home', component: HomeView, meta: { title: null } },
  { path: '/events', name: 'events', component: EventsView,
    meta: { title: "What's On", description: 'Talks, hardware nights, socials and the careers programme run by EESoc at Imperial College London.' } },
  { path: '/events/:slug', name: 'event', component: EventView, meta: { title: 'Event' } },
  { path: '/resources', name: 'resources', component: ResourcesView,
    meta: { title: 'Resources', description: 'Past papers, module feedback and student-written advice for Imperial EEE and EIE.' } },
  { path: '/resources/:slug', name: 'resource', component: ResourcesView, meta: { title: 'Resources' } },
  { path: '/jobs', name: 'jobs', component: JobsView,
    meta: { title: 'Open roles', description: 'Graduate roles, internships and placements from the companies that sponsor EESoc at Imperial College London.' } },
  { path: '/sponsors', name: 'sponsors', component: SponsorsView,
    meta: { title: 'Sponsors', description: 'The companies that support EESoc — workshops, careers events and the hackathon.' } },
  { path: '/lockers', name: 'lockers', component: LockersView,
    meta: { title: 'Lockers', description: 'Book an EESoc locker in the EEE building at Imperial College London.' } },
  // Two paths, one view. /unsubscribe is what goes in every bulk email, so it
  // must be short, stable and never require a session; /email is where the
  // same page is reached from the footer. Both are noindex — there is nothing
  // for a search engine here, and an indexed unsubscribe page is a page a
  // crawler can POST to.
  { path: '/email', name: 'email', component: EmailView, meta: { title: 'Email preferences', noindex: true } },
  { path: '/unsubscribe', name: 'unsubscribe', component: EmailView, meta: { title: 'Unsubscribe', noindex: true } },
  // The newsletter confirmation link. Confirms on a press, not on load, so a
  // mail scanner pre-fetching the URL signs nobody up.
  { path: '/subscribe', name: 'subscribe', component: SubscribeView, meta: { title: 'Newsletter', noindex: true } },
  { path: '/portal', name: 'portal', component: PortalView, meta: { title: 'Sponsor portal', requires: 'sponsor', noindex: true } },
  { path: '/admin/events/:id', name: 'admin-event', component: AdminEventView, meta: { title: 'Event bookings', requires: 'committee', noindex: true } },
  { path: '/admin', name: 'admin', component: AdminView, meta: { title: 'Committee admin', requires: 'committee', noindex: true } },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
  scrollBehavior(to, from) {
    // Staying on the same route and only changing a param (a resources
    // category, an event slug) is a content swap, not a navigation — scrolling
    // to the top there is what makes it read as a flicker.
    if (to.name === from.name && to.name === 'resource') return false
    return { top: 0 }
  },
})

// Gate the two private sections. This is convenience, not security — every
// endpoint behind them is enforced server-side too, because a route guard is
// just a hidden link to anyone with a keyboard.
router.beforeEach(async (to) => {
  if (!to.meta?.requires) return true
  if (!session.loaded) await refresh()
  // 'committee' rather than 'admin': a committee member with no role at all
  // still has one thing to do in there — their own card. The page itself shows
  // only what their role covers.
  if (to.meta.requires === 'committee' && !hasAdminArea.value) return { path: '/' }
  if (to.meta.requires === 'sponsor' && !session.isSponsor && !session.capabilities.includes('sponsors')) {
    return { path: '/' }
  }
  return true
})

router.afterEach((to) => {
  applySeo({
    title: to.meta?.title,
    description: to.meta?.description,
    path: to.fullPath,
    noindex: to.meta?.noindex,
  })
})

export default router
