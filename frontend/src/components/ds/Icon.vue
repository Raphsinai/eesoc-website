<script setup>
// Renders a Lucide outline glyph. The original design system loaded Lucide from
// a CDN and masked it; here we bundle `lucide-vue-next` so the site works offline
// and tree-shakes to only the icons used. Same visual result: 2px outline glyphs
// that inherit currentColor.
import { computed } from 'vue'
import {
  Search, ChevronLeft, ChevronRight, ChevronUp, ChevronDown,
  Calendar, Clock, MapPin, X, Instagram, Linkedin, Github, Mail,
  BookOpen, FileText, Folder, ExternalLink, Cpu, Users, Check, Zap, ArrowRight,
  Menu,
} from 'lucide-vue-next'

const registry = {
  'search': Search,
  'menu': Menu,
  'chevron-left': ChevronLeft,
  'chevron-right': ChevronRight,
  'chevron-up': ChevronUp,
  'chevron-down': ChevronDown,
  'calendar': Calendar,
  'clock': Clock,
  'map-pin': MapPin,
  'x': X,
  'instagram': Instagram,
  'linkedin': Linkedin,
  'github': Github,
  'mail': Mail,
  'book-open': BookOpen,
  'file-text': FileText,
  'folder': Folder,
  'external-link': ExternalLink,
  'cpu': Cpu,
  'users': Users,
  'check': Check,
  'zap': Zap,
  'arrow-right': ArrowRight,
}

const props = defineProps({
  name: { type: String, required: true },
  size: { type: [Number, String], default: 18 },
  strokeWidth: { type: [Number, String], default: 2 },
  label: { type: String, default: '' },
})

const glyph = computed(() => registry[props.name] || null)
</script>

<template>
  <component
    :is="glyph"
    v-if="glyph"
    :size="Number(size)"
    :stroke-width="Number(strokeWidth)"
    :role="label ? 'img' : 'presentation'"
    :aria-label="label || undefined"
    :aria-hidden="label ? undefined : 'true'"
    style="flex:none; display:inline-block; vertical-align:middle"
  />
</template>
