// Mailpit helper for the e2e specs: reads the newest login code mailed to an
// address, the way `login_code_from_mailpit` in tests/conftest.py does for the
// pytest suites. Kept in one place so both suites cannot drift on what
// "the code in the email" means.
//
// CommonJS on purpose: this file lives in tests/e2e, under the repo-root
// package (see the root package.json — "type": "commonjs"), and is require()d
// by the specs. ESM `export` syntax here would fail to parse.

const CODE_RE = /\b(\d{6})\b/

// The login-code email puts the code in the subject and both bodies; joining
// them avoids caring which part a mail client rendered.
function messageText(message) {
  return ['Subject', 'Text', 'HTML', 'Snippet']
    .map((key) => (typeof message[key] === 'string' ? message[key] : ''))
    .join(' ')
}

// The newest message to `to`, or an error once the timeout passes. The api
// sends login-code mail in the background after the 202, so polling is
// expected.
async function fetchLatestCode({
  mailpitBase = 'http://localhost:8025',
  to,
  timeout = 30000,
} = {}) {
  if (!to) throw new Error('fetchLatestCode needs a `to` address')

  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    const listing = await fetch(`${mailpitBase}/api/v1/messages?limit=100`)
    const { messages = [] } = await listing.json()
    // Mailpit lists newest first, so the first match is the message we want.
    for (const summary of messages) {
      const addresses = (summary.To || [])
        .map((r) => r.Address || '')
        .join(' ')
        .toLowerCase()
      if (!addresses.includes(to.toLowerCase())) continue

      const detail = await fetch(`${mailpitBase}/api/v1/message/${summary.ID}`)
      const message = await detail.json()
      const match = CODE_RE.exec(messageText(message))
      if (match) return match[1]
    }
    await new Promise((resolve) => setTimeout(resolve, 1000))
  }
  throw new Error(`no login code arrived for ${to} within ${timeout}ms`)
}

module.exports = { fetchLatestCode }
