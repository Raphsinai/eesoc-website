// Session state.
//
// A plain reactive module rather than Pinia — the app has exactly one piece of
// shared state and adding a store library for it would be more moving parts
// than it saves.

import { computed, reactive } from 'vue'

import api from '@/api'

export const state = reactive({
  loaded: false,
  authenticated: false,
  user: null,
  roles: [],
  isMember: false,
  isAdmin: false,
  isSponsor: false,
  sponsorIds: [],
  // The committee card this person may edit (by login), whether or not they
  // hold an admin role.
  committeeLogin: null,
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
  state.committeeLogin = data?.committee_login ?? null
  state.academicYear = data?.academic_year ?? null
  state.devLogin = Boolean(data?.dev_login)
  state.needsName = Boolean(data?.needs_name)
  state.loaded = true
}

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


/** Fill in a name after sign-in, for the external guests who need one. */
export async function saveName(firstName, lastName) {
  await api.patch('/auth/me', null, {
    first_name: firstName || null,
    last_name: lastName || null,
  })
  return refresh()
}
