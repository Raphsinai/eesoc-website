// Per-route SEO.
//
// A single-page app only ever ships one index.html, so without this every route
// shares the homepage's title and description — which is exactly what eesoc.com
// does today (one description on all twelve pages, so Google has nothing
// distinct to show for any of them).
//
// Crawlers execute JavaScript, so updating tags on navigation works; it is
// still worth prerendering later if search traffic matters.

const SITE = 'EESoc - Imperial College London'
const DEFAULT_DESCRIPTION =
  "Welcome to Imperial's Electrical Engineering society! Here you can find " +
  'upcoming events, our latest sponsors, and resources to assist with your studies.'

// Public origin, used for canonical and og:url. Overridable at build time
// because staging must not advertise production URLs to crawlers.
const ORIGIN = import.meta.env.VITE_SITE_ORIGIN || 'https://eesoc.com'

function setMeta(selector, attribute, value) {
  if (!value) return
  let tag = document.head.querySelector(selector)
  if (!tag) {
    tag = document.createElement('meta')
    const [key, name] = selector.replace(/[[\]"']/g, '').split('=')
    tag.setAttribute(key.replace('meta', '').trim() || 'name', name)
    document.head.appendChild(tag)
  }
  tag.setAttribute(attribute ?? 'content', value)
}

function setLink(rel, href) {
  let tag = document.head.querySelector(`link[rel="${rel}"]`)
  if (!tag) {
    tag = document.createElement('link')
    tag.setAttribute('rel', rel)
    document.head.appendChild(tag)
  }
  tag.setAttribute('href', href)
}

/**
 * @param {{title?: string, description?: string, path?: string, image?: string,
 *          type?: string, noindex?: boolean}} options
 */
export function applySeo({ title, description, path, image, type, noindex } = {}) {
  // eesoc.com's pattern: "<Page> | EESoc - Imperial College London", with the
  // homepage using the bare site name.
  const fullTitle = title ? `${title} | ${SITE}` : SITE
  const desc = description || DEFAULT_DESCRIPTION
  const url = `${ORIGIN}${path || window.location.pathname}`
  const card = image || `${ORIGIN}/assets/og-card.png`

  document.title = fullTitle
  setMeta('meta[name="description"]', 'content', desc)
  setMeta('meta[property="og:title"]', 'content', fullTitle)
  setMeta('meta[property="og:description"]', 'content', desc)
  setMeta('meta[property="og:url"]', 'content', url)
  setMeta('meta[property="og:image"]', 'content', card)
  setMeta('meta[property="og:type"]', 'content', type || 'website')
  setMeta('meta[name="twitter:title"]', 'content', fullTitle)
  setMeta('meta[name="twitter:description"]', 'content', desc)
  setMeta('meta[name="twitter:image"]', 'content', card)
  setLink('canonical', url)

  // Admin and the sponsor portal must never be indexed: they are private, and
  // a crawler following them would only ever record a login wall.
  let robots = document.head.querySelector('meta[name="robots"]')
  if (noindex) {
    if (!robots) {
      robots = document.createElement('meta')
      robots.setAttribute('name', 'robots')
      document.head.appendChild(robots)
    }
    robots.setAttribute('content', 'noindex, nofollow')
  } else if (robots) {
    robots.remove()
  }
}

/** Structured data for a single event, so it can appear as a rich result. */
export function applyEventSchema(event) {
  const existing = document.getElementById('event-schema')
  if (existing) existing.remove()
  if (!event) return

  const script = document.createElement('script')
  script.type = 'application/ld+json'
  script.id = 'event-schema'
  script.textContent = JSON.stringify({
    '@context': 'https://schema.org',
    '@type': 'Event',
    name: event.title,
    description: event.description || undefined,
    startDate: event.startsAt,
    endDate: event.endsAt || undefined,
    eventStatus: event.soldOut
      ? 'https://schema.org/EventScheduled'
      : 'https://schema.org/EventScheduled',
    eventAttendanceMode: 'https://schema.org/OfflineEventAttendanceMode',
    location: {
      '@type': 'Place',
      name: event.venue || 'Imperial College London',
      address: {
        '@type': 'PostalAddress',
        addressLocality: 'London',
        postalCode: 'SW7 2AZ',
        addressCountry: 'GB',
      },
    },
    organizer: { '@type': 'Organization', name: 'EESoc', url: ORIGIN },
    image: event.imageUrl ? [event.imageUrl] : undefined,
  })
  document.head.appendChild(script)
}

export { DEFAULT_DESCRIPTION, ORIGIN, SITE }
