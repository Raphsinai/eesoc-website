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
// user's place. "Unable to preload" IS matched: that is Vite's preload
// helper's message when a dependency of a lazy chunk (usually its CSS — see
// the vite:preloadError listener below) fails to fetch, which after a deploy
// is the same stale-chunk 404 as any other message here and recovers the
// same way. It cannot come from anything but a preload of our own assets.
const CHUNK_LOAD_FAILURE = /dynamically imported module|importing a module script failed|error loading dynamically imported module|unable to preload/i

// Key holding the epoch ms of the last recovery reload. Two channels carry
// it, written together and read wherever it is readable (see writeLastReload):
// sessionStorage, the natural home — it survives a reload and dies with the
// tab — but it THROWS in private-browsing modes and under policies that
// disable storage, exactly where a reload-loop guard matters most; and
// window.name, a plain property that ALSO survives a same-origin reload
// (browsers clear it on cross-origin navigation instead), needs no
// permission, and cannot be disabled. Nothing else in the app uses
// window.name, and the prefix keys it to this hook, so the two channels
// never collide with anything.
const RELOAD_KEY = 'eesoc.chunk-reload'
const RELOAD_NAME_PREFIX = `${RELOAD_KEY}:`

// A recovery reload is rate-limited to one per 10 seconds rather than being a
// one-shot flag: a tab that recovered once can recover again later (a second
// deploy while it is still open), but a persistently broken bundle cannot put
// the tab into an infinite reload loop — the second consecutive reload within
// 10 s is suppressed and the error surfaces instead.
const RELOAD_COOLDOWN_MS = 10000

function isChunkLoadFailure(message) {
  return typeof message === 'string' && CHUNK_LOAD_FAILURE.test(message)
}

// The last recovery-reload timestamp from whichever channel still holds one:
// 0 (long before `now`) means "never recovered".
function readLastReload() {
  let stamp = ''
  try {
    stamp = sessionStorage.getItem(RELOAD_KEY) || ''
  } catch {
    // Storage unavailable — fall through and read window.name instead.
  }
  if (!stamp && window.name.startsWith(RELOAD_NAME_PREFIX)) {
    stamp = window.name.slice(RELOAD_NAME_PREFIX.length)
  }
  return Number(stamp) || 0
}

function writeLastReload(ts) {
  try {
    sessionStorage.setItem(RELOAD_KEY, String(ts))
  } catch {
    // Storage unavailable or full — the window.name write below still
    // carries the guard, so the cooldown keeps holding after the reload.
  }
  try {
    // Written unconditionally (not only on the storage failure above): a
    // storage-less browser reads it back after the reload, and the
    // no-reload-loop guarantee then holds exactly as it does with storage.
    window.name = RELOAD_NAME_PREFIX + ts
  } catch {
    // A page that froze window.name cannot be guarded — but it is
    // pathological, and the reload this enables is the point of this
    // function, so it goes ahead regardless.
  }
}

function maybeRecover(err) {
  const message = err && (err.message || String(err))
  if (!isChunkLoadFailure(message)) return false

  const now = Date.now()
  if (now - readLastReload() <= RELOAD_COOLDOWN_MS) return false

  // Write the timestamp right before reloading: if the reload lands on a
  // still-broken bundle, the guard is already set for the next attempt —
  // through whichever channel survives the reload.
  writeLastReload(now)
  window.location.reload()
  return true
}

export function installChunkRecovery(router) {
  // Router navigations to lazily-loaded routes reject the navigation promise
  // with the chunk load error, which lands here.
  router.onError((err) => { maybeRecover(err) })

  // Vite's own channel for failed dynamic-import preloads: the __vitePreload
  // helper dispatches this on window when a dependency of a lazy chunk
  // fails to fetch — including its CSS, which does not always surface as a
  // router error. `event.payload` is the underlying error. The same failure
  // often reaches the router error handler too a moment later; the 10 s
  // cooldown inside maybeRecover makes that double-handling harmless —
  // only one reload happens either way.
  window.addEventListener('vite:preloadError', (event) => {
    maybeRecover(event.payload)
  })

  // Resource-load error events (a <script> the bundler injected failing to
  // download) do not bubble, so listen in the capture phase on window to see
  // them at all.
  window.addEventListener(
    'error',
    (event) => {
      // Only script loads matter here, and only our own: ordinary runtime
      // errors in the app are handled by their own code paths, and a failing
      // third-party script means that script is broken, not that our bundle
      // went stale — reloading for it would just lose the user's place.
      const target = event && event.target
      if (
        target && target.tagName === 'SCRIPT'
        && new URL(target.src, location.href).origin === location.origin
      ) {
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
