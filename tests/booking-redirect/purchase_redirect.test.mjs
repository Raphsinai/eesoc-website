import assert from 'node:assert/strict'
import { test } from 'node:test'
import { unionShopRedirect } from '../../frontend/src/utils/purchaseRedirect.js'

const shop = 'https://www-s.imperialcollegeunion.org/shop/product/3023-/'
const held = { status: 'held' }

test('paid reservations redirect to the intended Union product', () => {
  for (const action of ['buy', 'buy_and_supply_order']) {
    assert.equal(unionShopRedirect({ action, purchaseUrl: shop }, held), shop)
  }
})

test('free and already-confirmed bookings do not redirect', () => {
  assert.equal(unionShopRedirect({ action: 'none', purchaseUrl: shop }, { status: 'confirmed' }), null)
  assert.equal(unionShopRedirect({ action: 'buy', purchaseUrl: shop }, { status: 'confirmed' }), null)
  assert.equal(unionShopRedirect({ action: 'buy', purchaseUrl: shop }, null), null)
})

test('missing, malformed and non-Union URLs do not trigger navigation', () => {
  const bad = [null, '', 'not a link', 'javascript:alert(1)',
    'http://www-s.imperialcollegeunion.org/shop/product/3023-/',
    'https://imperialcollegeunion.org.evil.example/shop/product/3023-/',
    'https://example.com/sale']
  for (const purchaseUrl of bad) {
    assert.equal(unionShopRedirect({ action: 'buy', purchaseUrl }, held), null, String(purchaseUrl))
  }
})
