<script setup>
import Logo from '@/components/ds/Logo.vue'
import Icon from '@/components/ds/Icon.vue'
import IconButton from '@/components/ds/IconButton.vue'
import PinStrip from '@/components/ds/PinStrip.vue'
</script>

<template>
  <footer class="eesoc-dark site-footer">
    <PinStrip :length="14" />
    <div class="footer-grid">
      <div style="display:grid; gap:var(--space-4); align-content:start">
        <span class="site-footer__logo"><Logo variant="pins" :height="26" /></span>
        <span class="site-footer__addr">
          Electrical Engineering Society<br />Imperial College Union<br />South Kensington, London SW7 2BB
        </span>
      </div>

      <div class="site-footer__col">
        <span class="site-footer__eyebrow">Site</span>
        <router-link to="/resources" class="site-footer__link">Resources</router-link>
        <router-link to="/lockers" class="site-footer__link">Lockers</router-link>
        <router-link to="/sponsors" class="site-footer__link">Sponsors</router-link>
        <router-link to="/email" class="site-footer__link">Email preferences</router-link>
      </div>

      <div class="site-footer__col">
        <span class="site-footer__eyebrow">Sponsors</span>
        <router-link to="/portal" class="site-footer__link">Sponsor portal</router-link>
        <a href="#" class="site-footer__link" @click.prevent>Prospectus 2026–27</a>
        <a href="mailto:eesoc.vp@imperial.ac.uk" class="site-footer__link">eesoc.vp@imperial.ac.uk</a>
      </div>

      <div class="site-footer__col">
        <span class="site-footer__eyebrow">Follow</span>
        <!-- Instagram only. The LinkedIn and GitHub buttons went nowhere —
             an icon that looks like a link and does nothing is worse than no
             icon. Add them back when there are accounts to point at. -->
        <div style="display:flex; gap:var(--space-3)">
          <a
            href="https://instagram.com/eesoc"
            target="_blank"
            rel="noopener noreferrer"
            aria-label="EESoc on Instagram (@eesoc)"
            class="site-footer__social"
          >
            <Icon name="instagram" :size="18" />
            <span>@eesoc</span>
          </a>
        </div>
      </div>
    </div>
  </footer>
</template>

<style scoped>
.site-footer {
  background: var(--surface-page);
  color: var(--text-body);
}
.footer-grid {
  max-width: var(--container-xl);
  margin: 0 auto;
  padding: var(--space-16) var(--space-8);
  display: grid;
  grid-template-columns: 1.2fr 1fr 1fr 1fr;
  gap: var(--space-10);
}
.site-footer__logo {
  background: var(--white);
  padding: 10px 12px;
  border-radius: 8px;
  justify-self: start;
  line-height: 0;
}
.site-footer__addr {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  line-height: 1.7;
  color: var(--text-muted);
}
.site-footer__col {
  display: grid;
  gap: var(--space-3);
  align-content: start;
}
.site-footer__eyebrow {
  font: var(--type-eyebrow);
  letter-spacing: var(--tracking-label);
  text-transform: uppercase;
  color: var(--text-accent);
}
.site-footer__link {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
}

.site-footer__social {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font: var(--type-small);
  color: var(--text-muted);
  text-decoration: none;
}
.site-footer__social:hover {
  color: var(--text-strong);
}
</style>
