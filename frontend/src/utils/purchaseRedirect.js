// A successful paid reservation may go directly to the official Union shop.
// A missing or arbitrary external link never causes automatic navigation.
export function unionShopRedirect(next, reservation) {
  if (reservation?.status !== 'held' || !['buy', 'buy_and_supply_order'].includes(next?.action)) return null
  try {
    const url = new URL(next.purchaseUrl)
    const host = url.hostname.toLowerCase()
    if (url.protocol !== 'https:' || (host !== 'imperialcollegeunion.org' && !host.endsWith('.imperialcollegeunion.org'))) return null
    return url.href
  } catch {
    return null
  }
}
