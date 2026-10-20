// Session state.
//
// A plain reactive module rather than Pinia — the app has exactly one piece of
// shared state and adding a store library for it would be more moving parts
// than it saves.

import { computed, reactive } from 'vue'

import api from '@/api'
import { createPasskey, deviceName, getPasskey } from '@/utils/passkey'

export const state = reactive({
  loaded: false,
  authenticated: false,
  user: null,
  roles: [],
  isMember: false,
  isAdmin: false,
  isSponsor: false,
  sponsorIds: [],
  // The committee role held — superuser | admin | events | industry | null —
  // and the capabilities it carries. Used to decide what to render; the server
  // decides what is allowed.
  role: null,
  capabilities: [],
  // The committee card this person may edit, by login. Set for ordinary
  // committee members who hold no role at all.
  committeeLogin: null,
  // How many passkeys the account has. Zero is what makes the sign-in dialog
  // offer to save one.
  passkeys: 0,
  impersonating: false,
  impersonatorEmail: null,
  academicYear: null,
  devLogin: false,
  needsName: false,
})

function apply(data) {
  state.authenticated = Boolean(data?.authenticated)
  state.user = data?.user ?? null
  state.roles = data?.roles ?? []
  state.isMember = Boolean(data?.is_member)
  state.isAdmin = Boolean(data?.is_admin)
  state.isSponsor = Boolean(data?.is_sponsor)
  state.sponsorIds = data?.sponsor_ids ?? []
  state.role = data?.role ?? null
  state.capabilities = data?.capabilities ?? []
  state.committeeLogin = data?.committee_login ?? null
  state.passkeys = data?.passkeys ?? 0
  state.impersonating = Boolean(data?.impersonating)
  state.impersonatorEmail = data?.impersonator_email ?? null
  state.academicYear = data?.academic_year ?? null
  state.devLogin = Boolean(data?.dev_login)
  state.needsName = Boolean(data?.needs_name)
  state.loaded = true
}

/** Exact action permission; an old group name is a compatibility alias only. */
export function can(permission) {
  if (state.capabilities.includes(permission)) return true
  const scope = state.capabilities.includes('events.scope.all')
  const industry = state.capabilities.includes('events.scope.industry')
  if (permission === 'events' || permission === 'industry') {
    const requestedScope = permission === 'events' ? scope : industry
    return requestedScope && state.capabilities.some((p) => p.startsWith('events.') &&
      !['events.bookings.', 'events.checkin.', 'events.sync.', 'events.scope.'].some((prefix) => p.startsWith(prefix)))
  }
  if (permission === 'bookings' || permission === 'industry_bookings') {
    return (permission === 'bookings' ? scope : industry) &&
      state.capabilities.some((p) => p.startsWith('events.bookings.'))
  }
  const legacyPrefixes = {
    users: 'access.', passkey_links: 'passkeys.', sync: 'events.sync.',
    sponsors: 'sponsors.', resources: 'resources.', files: 'files.',
    committee: 'committee.', product_links: 'products.', lockers: 'lockers.',
    locker_allowances: 'lockers.allowances.', email: 'email.',
    whatsapp: 'whatsapp.', opportunities: 'opportunities.',
  }
  return Boolean(legacyPrefixes[permission] &&
    state.capabilities.some((p) => p.startsWith(legacyPrefixes[permission])))
}

/** UI visibility for a read-only category tab, not a grant of a category. */
export function anyPermission(prefix) {
  return state.capabilities.some((p) => p.startsWith(prefix))
}

/** Anything to do in the admin area at all — a role, or a card of their own. */
export const hasAdminArea = computed(
  () => state.isAdmin || Boolean(state.committeeLogin),
)

/** Called once on boot, and again after any sign-in or sign-out. */
export async function refresh() {
  try {
    apply(await api.get('/auth/session'))
  } catch {
    // A failed session probe must not break the page — treat it as signed out.
    apply({ authenticated: false })
  }
  return state
}

export async function requestCode(email, extra = {}) {
  return api.post('/auth/request-code', { email, ...extra })
}

export async function verifyCode(email, code, extra = {}) {
  const data = await api.post('/auth/verify-code', { email, code, ...extra })
  apply(data)
  return data
}

export async function logout() {
  try {
    await api.post('/auth/logout')
  } finally {
    await refresh()
  }
}

export const displayName = computed(
  () => state.user?.name || state.user?.email?.split('@')[0] || 'Account',
)

export default state


/** Development-only sign-in. The endpoint 404s unless the server is explicitly
 *  in development mode, so this can never work against production. */
export async function devLogin(email, makeAdmin = true) {
  const data = await api.post('/auth/dev-login', { email, make_admin: makeAdmin })
  apply(data)
  return data
}


/** Hand the browser back to the superuser who started impersonating.
 *  Deliberately on /auth: the session calling it is the impersonated one and
 *  has none of their access. */
export async function stopImpersonating() {
  const data = await api.post('/auth/stop-impersonating')
  apply(data)
  return data
}


/** Fill in a name after sign-in, for the external guests who need one. */
export async function saveName(firstName, lastName) {
  await api.patch('/auth/me', null, {
    first_name: firstName || null,
    last_name: lastName || null,
  })
  return refresh()
}


/** Sign in with a passkey: the server's challenge, the browser's sheet, the
 *  server's check. No address is typed — the passkey says whose it is.
 *  Throws the browser's own error when the sheet is closed; `cancelled()` in
 *  utils/passkey tells that apart from a real failure. */
export async function passkeySignIn() {
  const options = await api.post('/auth/passkey/options')
  const credential = await getPasskey(options)
  const data = await api.post('/auth/passkey/verify', { credential })
  apply(data)
  return data
}


/** Save a passkey on the signed-in account, then refresh so `passkeys` is
 *  counted and the offer stops appearing. */
export async function addPasskey() {
  const options = await api.post('/auth/passkeys/options')
  const credential = await createPasskey(options)
  const res = await api.post('/auth/passkeys', { credential, name: deviceName() })
  await refresh()
  return res
}


/** The setup-link half: save a passkey on the account a committee-made link
 *  names, which signs the browser in as that account. */
export async function registerWithLink(token) {
  const options = await api.post('/auth/passkey-link/options', { token })
  const credential = await createPasskey(options)
  const data = await api.post('/auth/passkey-link/register', {
    token,
    credential,
    name: deviceName(),
  })
  apply(data)
  return data
}
