import { createRouter, createWebHistory } from 'vue-router'

// The home page is the only eagerly imported view: it is the first paint of
// every visit, so splitting it into its own chunk would buy nothing and
// would add a round trip before anything appears on screen.
import HomeView from '@/views/HomeView.vue'
import session, { can, hasAdminArea, refresh } from '@/stores/session'
import { applySeo } from '@/seo'

// Route-level code splitting: every other route below loads its view with a
// dynamic import, so each one compiles to its own hashed chunk under
// /assets/ that the browser fetches on first navigation. This is what makes
// this section's two halves meaningful rather than theoretical: chunk
// continuity (the frontend image unions the previous builds' /assets, see
// the Dockerfile) and chunk-load recovery (src/chunk-recovery.js) are both
// exactly the case of a surviving tab lazily importing a chunk from before
// a deploy — if everything were eagerly bundled into the entry chunk there
// would be nothing to 404 after a deploy and nothing for either mechanism
// to do. It is also a genuine first-load win: AdminView alone is ~2.4k
// lines, which nobody reading the home page should have to download.
//
// Routes that share a view (/resources and /resources/:slug, /email and
// /unsubscribe) share one loader, so a view is always exactly one chunk and
// one fetch no matter which path reaches it.

const ResourcesView = () => import('@/views/ResourcesView.vue')
const SponsorsView = () => import('@/views/SponsorsView.vue')
const LockersView = () => import('@/views/LockersView.vue')
const PortalView = () => import('@/views/PortalView.vue')
const SocietyPortalView = () => import('@/views/SocietyPortalView.vue')
const EventsView = () => import('@/views/EventsView.vue')
const EventView = () => import('@/views/EventView.vue')
const AdminView = () => import('@/views/AdminView.vue')
const AdminEventView = () => import('@/views/AdminEventView.vue')
const AdminSponsorView = () => import('@/views/AdminSponsorView.vue')
const JobsView = () => import('@/views/JobsView.vue')
const OpportunitiesView = () => import('@/views/OpportunitiesView.vue')
const EmailView = () => import('@/views/EmailView.vue')
const SubscribeView = () => import('@/views/SubscribeView.vue')
const CheckinView = () => import('@/views/CheckinView.vue')
const TicketView = () => import('@/views/TicketView.vue')
const AccountView = () => import('@/views/AccountView.vue')
const PasskeySetupView = () => import('@/views/PasskeySetupView.vue')
const PhotoStudioView = () => import('@/views/PhotoStudioView.vue')
const PhotoGalleryView = () => import('@/views/PhotoGalleryView.vue')

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
  { path: '/opportunities', name: 'opportunities', component: OpportunitiesView,
    meta: { title: 'Opportunities', description: 'Community-submitted projects, internships, competitions, research and more, reviewed by the EESoc committee.' } },
  { path: '/opportunities/:id(\\d+)', name: 'opportunity', component: OpportunitiesView,
    meta: { title: 'Opportunity' } },
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
  { path: '/societies', name: 'society-portal', component: SocietyPortalView, meta: { title: 'Society committee', noindex: true } },
  { path: '/societies/:societyId/events/:id/bookings', name: 'society-event-bookings', component: AdminEventView, meta: { title: 'Society event bookings', noindex: true } },
  { path: '/societies/:societyId/checkin/:eventId?', name: 'society-checkin', component: CheckinView, meta: { title: 'Society door check-in', noindex: true } },
  { path: '/admin/events/:id', name: 'admin-event', component: AdminEventView, meta: { title: 'Event bookings', requires: 'committee', noindex: true } },
  { path: '/admin/sponsors/:id', name: 'admin-sponsor', component: AdminSponsorView, meta: { title: 'Sponsor agreement', requires: 'committee', noindex: true } },
  { path: '/admin', name: 'admin', component: AdminView, meta: { title: 'Committee admin', requires: 'committee', noindex: true } },
  // The door. 'committee' covers anybody on the roster, role or not — the API
  // checks the same thing.
  { path: '/photo-studio', name: 'photo-studio', component: PhotoStudioView,
    meta: { title: 'Photo print studio', requires: 'committee', noindex: true } },
  { path: '/photos/:token', name: 'photo-gallery', component: PhotoGalleryView,
    meta: { title: 'Your photos', noindex: true } },
  { path: '/checkin', name: 'checkin', component: CheckinView, meta: { title: 'Door check-in', requires: 'committee', noindex: true } },
  { path: '/checkin/:eventId', name: 'checkin-event', component: CheckinView, meta: { title: 'Door check-in', requires: 'committee', noindex: true } },
  // A ticket. Public — the token in the URL is the credential.
  { path: '/ticket/:token', name: 'ticket', component: TicketView, meta: { title: 'Your ticket', noindex: true } },
  // Your passkeys. Not guarded: signed out, the page offers to sign in rather
  // than bouncing to the home page.
  { path: '/account', name: 'account', component: AccountView, meta: { title: 'Your account', noindex: true } },
  // Where a committee-made passkey link lands. The token is in the fragment.
  { path: '/passkey-setup', name: 'passkey-setup', component: PasskeySetupView, meta: { title: 'Set up a passkey', noindex: true } },
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
  if (to.meta.requires === 'sponsor' && !session.isSponsor && !can('sponsors.portal.view')) {
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
