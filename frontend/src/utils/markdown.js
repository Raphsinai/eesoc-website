// Minimal markdown -> HTML.
//
// The resource bodies scraped from eesoc.com use links, bold, italics, inline
// code and bullet lists — nothing more. A full markdown library would be ~50 KB
// for four constructs.
//
// Everything is escaped FIRST and only the recognised constructs are then
// re-introduced, so admin-authored content cannot inject markup. That ordering
// is the whole security model here: this output goes through v-html.

function escapeHtml(text) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

// Only http(s) and mailto. Blocks javascript: and data: URLs, which would
// otherwise be a scripting vector via an ordinary-looking link.
function safeUrl(url) {
  const trimmed = (url || '').trim()
  return /^(https?:\/\/|mailto:|\/)/i.test(trimmed) ? trimmed : '#'
}

export function renderMarkdown(source) {
  if (!source) return ''
  let html = escapeHtml(source)

  html = html.replace(/`([^`]+)`/g, '<code>$1</code>')
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, label, url) => {
    const href = safeUrl(url)
    const external = /^https?:\/\//i.test(href)
    return `<a href="${href}"${external ? ' target="_blank" rel="noopener noreferrer"' : ''}>${label}</a>`
  })
  html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  html = html.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')

  // Group consecutive "- " lines into a single list.
  const blocks = html.split(/\n{2,}/).map((block) => {
    const lines = block.split('\n')
    if (lines.every((l) => l.trim().startsWith('- ') || !l.trim())) {
      const items = lines
        .filter((l) => l.trim())
        .map((l) => `<li>${l.trim().slice(2)}</li>`)
        .join('')
      return items ? `<ul>${items}</ul>` : ''
    }
    return `<p>${block.replace(/\n/g, '<br />')}</p>`
  })

  return blocks.filter(Boolean).join('')
}

export default renderMarkdown
