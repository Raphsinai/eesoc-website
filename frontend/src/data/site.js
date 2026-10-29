// All EESoc site content, ported verbatim from the design mockup
// (EESoc Website.dc.html). This is placeholder copy from the design system —
// swap for live data when the society's backend is wired up.

export const events = [
  { id: 1, category: 'Careers', title: 'Careers Fair 2026', date: 'Thu 22 Oct', time: '12:00 – 17:00', venue: 'EEE Foyer', status: 'Free entry', statusTone: 'success' },
  { id: 2, category: 'Hardware', title: 'Soldering Clinic', date: 'Wed 12 Nov', time: '18:00 – 20:00', venue: 'EEE 403', status: '12 places', statusTone: 'accent' },
  { id: 3, category: 'Talks', title: 'Silicon After Moore', date: 'Tue 18 Nov', time: '17:00 – 18:30', venue: 'LT 508', status: 'Free entry', statusTone: 'success' },
  { id: 4, category: 'Social', title: 'The Shard', date: 'Fri 13 Mar', time: '19:00 – late', venue: 'Level 33', status: 'Waitlist', statusTone: 'neutral' },
  { id: 5, category: 'Workshops', title: 'IMC SystemVerilog day', date: 'Sat 21 Feb', time: '10:00 – 17:00', venue: 'Level 4 Lab', status: 'Members only', statusTone: 'brand' },
  { id: 6, category: 'Social', title: 'Annual Dinner', date: 'Fri 06 Feb', time: '19:00 – late', venue: 'Sherfield', status: 'Sold out', statusTone: 'danger' },
]

export const committee = [
  { role: 'President', name: 'Nadia Haldar', email: 'eesoc@imperial.ac.uk' },
  { role: 'VP Sponsorship', name: 'Tom Vickers', email: 'eesoc.vp@imperial.ac.uk' },
  { role: 'Treasurer', name: 'Ife Adeyemi', email: 'eesoc.treasurer@imperial.ac.uk' },
  { role: 'Secretary', name: 'Marta Sowa', email: 'eesoc.sec@imperial.ac.uk' },
  { role: 'Events Officer', name: 'Danny Okafor', email: 'eesoc.events@imperial.ac.uk' },
  { role: 'Academic Officer', name: 'Wei Lin Tan', email: 'eesoc.academic@imperial.ac.uk' },
  { role: 'WiEE Chair', name: 'Priya Raman', email: 'wiee@imperial.ac.uk' },
  { role: 'Publicity', name: 'Josef Brandt', email: 'eesoc.media@imperial.ac.uk' },
]

// Sponsors now come from the database — see SponsorsView.vue. The invented
// list that used to live here (IMC Trading, Synopsys, Arm, Nordic, National
// Grid) was mockup content and has been removed so it cannot be published by
// accident. Add real sponsors through the admin dashboard.
export const sponsors = []

export const cats = [
  { id: 'papers', label: 'Past papers', count: 42, title: 'Past papers', blurb: 'Every paper the department has released since 2018, with mark schemes where we have them. Missing a year? Email the academic officer and we will chase it.', kind: 'table' },
  { id: 'notes', label: 'Notes & handbooks', count: 18, title: 'Notes & handbooks', blurb: 'Student-written notes, module handbooks and the first-year survival guide. Contributions welcome — credit stays on the file.', kind: 'cards' },
  { id: 'software', label: 'Software & tools', count: 11, title: 'Software & tools', blurb: 'Licences the department pays for, and the free tools we actually use. Log in for the licence keys.', kind: 'cards' },
  { id: 'careers', label: 'Careers & CVs', count: 9, title: 'Careers & CVs', blurb: 'The CV template that got people hired, interview notes by firm, and the internship timeline for each stream.', kind: 'cards' },
  { id: 'lab', label: 'Lab & kit', count: 7, title: 'Lab & kit', blurb: 'Bench guides, the oscilloscope cheat sheet, and what you can borrow from the EESoc cupboard.', kind: 'cards' },
]

export const content = {
  papers: [
    { code: 'EE1-01', title: 'Analysis of Circuits', years: ['2025', '2024', '2023'] },
    { code: 'EE1-05', title: 'Digital Electronics I', years: ['2025', '2024', '2023'] },
    { code: 'EE2-03', title: 'Signals & Linear Systems', years: ['2025', '2024'] },
    { code: 'EE2-07', title: 'Control Engineering', years: ['2025', '2023', '2022'] },
    { code: 'EE3-12', title: 'Digital Signal Processing', years: ['2025', '2024'] },
    { code: 'EE4-06', title: 'Advanced Electronic Devices', years: ['2024', '2023'] },
  ],
  notes: [
    { meta: 'First year', title: 'Survival guide', desc: 'What to buy, what to skip, and which labs eat your weekends.', icon: 'book-open' },
    { meta: 'EE1-01', title: 'Circuits notes, full year', desc: "Written by last year's cohort. 62 pages, worked examples throughout.", icon: 'file-text' },
    { meta: 'EE2-03', title: 'Signals summary sheets', desc: 'Four sides of transforms, tables and the identities you forget.', icon: 'file-text' },
    { meta: 'Department', title: 'Module handbooks 2026–27', desc: "Official handbooks for both streams, mirrored here so links don't rot.", icon: 'folder' },
  ],
  software: [
    { meta: 'Licence', title: 'MATLAB & Simulink', desc: 'Free for all Imperial students. Activation walk-through inside.', icon: 'external-link' },
    { meta: 'Licence', title: 'Altium Designer', desc: 'Student licence via the department. Renew each October.', icon: 'external-link' },
    { meta: 'Free', title: 'KiCad 9', desc: 'What most of us use for society projects. Library of our footprints included.', icon: 'cpu' },
    { meta: 'Free', title: 'Vivado ML Standard', desc: 'For the FPGA modules and the hackathon boards.', icon: 'cpu' },
  ],
  careers: [
    { meta: 'Template', title: 'The EESoc CV', desc: 'One page, LaTeX and Word versions, annotated with what recruiters skim.', icon: 'file-text' },
    { meta: 'Notes', title: 'Interview notes by firm', desc: 'Crowd-sourced rounds for 24 firms. Add yours after you interview.', icon: 'users' },
    { meta: 'Timeline', title: 'Internship calendar', desc: 'When applications open for each stream, month by month.', icon: 'calendar' },
  ],
  lab: [
    { meta: 'Cheat sheet', title: 'Oscilloscope in ten minutes', desc: 'Triggering, probes, and why your signal looks like that.', icon: 'zap' },
    { meta: 'Cupboard', title: 'What you can borrow', desc: 'Irons, meters, breadboards, two logic analysers. Sign out in EEE 403.', icon: 'cpu' },
    { meta: 'Safety', title: 'Lab rules, short version', desc: 'The four things that get you sent home. Read before your first session.', icon: 'check' },
  ],
}

// Primary site navigation → maps to router routes.
export const nav = [
  { id: 'home', label: 'Home', to: '/' },
  { id: 'events', label: "What's on", to: '/events' },
  { id: 'resources', label: 'Resources', to: '/resources' },
  { id: 'sponsors', label: 'Sponsors', to: '/sponsors' },
  { id: 'jobs', label: 'Open roles', to: '/jobs' },
  { id: 'opportunities', label: 'Opportunities', to: '/opportunities' },
  { id: 'lockers', label: 'Lockers', to: '/lockers' },
  { id: 'portal', label: 'Sponsor portal', to: '/portal' },
]

export const portalNav = [
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'profile', label: 'Your card' },
  { id: 'jobs', label: 'Job listings' },
  { id: 'calendar', label: 'Event calendar' },
  { id: 'request', label: 'Request an event' },
  { id: 'marketing', label: 'Marketing' },
  { id: 'contacts', label: 'Contacts' },
  { id: 'merch', label: 'Brand & merch' },
  { id: 'messages', label: 'Messages' },
  { id: 'access', label: 'Access management' },
]

// Flat search index across the whole site — powers the header search.
export function buildSearchIndex() {
  const index = []
  events.forEach((e) => index.push({ label: `${e.title} · ${e.date}`, kind: 'Event', to: '/' }))
  cats.forEach((c) => {
    (content[c.id] || []).forEach((i) =>
      index.push({ label: i.title || `${i.code} ${i.title}`, kind: c.label, to: '/resources', cat: c.id }),
    )
  })
  content.papers.forEach((p) => index.push({ label: `${p.code} ${p.title}`, kind: 'Past paper', to: '/resources', cat: 'papers' }))
  sponsors.forEach((s) => index.push({ label: s.name, kind: 'Sponsor', to: '/sponsors' }))
  index.push({ label: 'Book a locker', kind: 'Page', to: '/lockers' })
  return index
}
