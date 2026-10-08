// Recovery for chunk-load failures after a deploy.
//
// After every deploy the old hashed chunks are gone from the server (or only
// kept for a bounded window — see the Dockerfile), but a tab opened before the
// deploy still references them by URL. When such a tab lazily navigates, the
// dynamic import of the route component fails and the user would otherwise be
// left on a dead route. Reloading the page is the right fix: index.html is
// served with no-cache (see nginx.conf), so a reload picks up the new bundle.

// Only errors phrased like a module/chunk load failure are recoverable.
// "Failed to fetch" is deliberately NOT matched: that message also comes from
// ordinary API request failures, and reloading the page on those is wrong —
// the data layer has its own error handling and a reload would just lose the
// user's place.
const CHUNK_LOAD_FAILURE = /dynamically imported module|importing a module script failed|error loading dynamically imported module/i

// sessionStorage key holding the epoch ms of the last recovery reload.
const RELOAD_KEY = 'eesoc.chunk-reload'

// A recovery reload is rate-limited to one per 10 seconds rather than being a
// one-shot flag: a tab that recovered once can recover again later (a second
// deploy while it is still open), but a persistently broken bundle cannot put
// the tab into an infinite reload loop — the second consecutive reload within
// 10 s is suppressed and the error surfaces instead.
const RELOAD_COOLDOWN_MS = 10000

function isChunkLoadFailure(message) {
  return typeof message === 'string' && CHUNK_LOAD_FAILURE.test(message)
}

function maybeRecover(err) {
  const message = err && (err.message || String(err))
  if (!isChunkLoadFailure(message)) return false

  const now = Date.now()
  let lastReload = 0
  try {
    lastReload = Number(sessionStorage.getItem(RELOAD_KEY)) || 0
  } catch {
    // sessionStorage can throw (privacy mode, disabled storage); treat it as
    // "no previous reload" so recovery still happens once.
  }
  if (now - lastReload <= RELOAD_COOLDOWN_MS) return false

  // Write the timestamp right before reloading: if the reload lands on a
  // still-broken bundle, the guard is already set for the next attempt.
  try {
    sessionStorage.setItem(RELOAD_KEY, String(now))
  } catch {
    // Same reasoning as above — proceed with the reload regardless.
  }
  window.location.reload()
  return true
}

export function installChunkRecovery(router) {
  // Router navigations to lazily-loaded routes reject the navigation promise
  // with the chunk load error, which lands here.
  router.onError((err) => { maybeRecover(err) })

  // Resource-load error events (a <script> the bundler injected failing to
  // download) do not bubble, so listen in the capture phase on window to see
  // them at all.
  window.addEventListener(
    'error',
    (event) => {
      // Only module script loads matter here; ordinary runtime errors in the
      // app are handled by their own code paths and must not trigger a reload.
      if (event && event.target && event.target.tagName === 'SCRIPT') {
        maybeRecover(new Error(event.message || 'importing a module script failed'))
      }
    },
    true,
  )

  // Dynamic import()s made outside router navigation (e.g. in component code)
  // surface as unhandled promise rejections instead of error events.
  window.addEventListener('unhandledrejection', (event) => {
    maybeRecover(event.reason)
  })
}
