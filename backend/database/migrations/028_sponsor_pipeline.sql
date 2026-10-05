-- The 2026-27 sponsor pipeline, with logos.
--
-- Twelve companies, all at "proposal drafted" — none of them has signed. They
-- go in with active = false so they show on the committee dashboard and stay
-- off the public /sponsors page: SponsorsView already refuses to publish a
-- sponsor EESoc does not have, and a row saying "TBC" on the live site is the
-- same mistake by another route. Flip `active` once the agreement is signed.
--
-- Everything but name, slug and logo is a deliberate placeholder for the
-- sponsorship officer to fill in. `sector` is the only one with a real value,
-- because it is a fact about the company rather than about the deal.
--
-- Logos are committed under frontend/public/assets/sponsors/ and served as
-- static files, so these rows need no upload and survive a fresh volume.
-- Provenance for every file is in sponsor_logos/SOURCES.md.

INSERT INTO public.sponsors (slug, name, sector, strand, one_line, blurb, with_us, site, logo_url, active, sort_order)
VALUES
    ('optiver',      'Optiver',                   'Quantitative Trading', 'TBC', 'TBC', '', '{}', 'optiver.com',     '/assets/sponsors/optiver.svg',      false, 10),
    ('jump-trading', 'Jump Trading',              'Quantitative Trading', 'TBC', 'TBC', '', '{}', 'jumptrading.com', '/assets/sponsors/jump-trading.svg', false, 20),
    ('entangl',      'Entangl',                   'Deep Tech',            'TBC', 'TBC', '', '{}', 'entangl.com',     '/assets/sponsors/entangl.svg',      false, 30),
    ('jane-street',  'Jane Street',               'Quantitative Trading', 'TBC', 'TBC', '', '{}', 'janestreet.com',  '/assets/sponsors/jane-street.svg',  false, 40),
    ('qualcomm',     'Qualcomm',                  'Semiconductors',       'TBC', 'TBC', '', '{}', 'qualcomm.com',    '/assets/sponsors/qualcomm.svg',     false, 50),
    ('cirrus-logic', 'Cirrus Logic',              'Semiconductors',       'TBC', 'TBC', '', '{}', 'cirrus.com',      '/assets/sponsors/cirrus-logic.svg', false, 60),
    ('qrt',          'Qube Research & Technologies', 'Quantitative Trading', 'TBC', 'TBC', '', '{}', 'qube-rt.com',  '/assets/sponsors/qrt.svg',          false, 70),
    ('technelec',    'Technelec',                 'Electrical Engineering', 'TBC', 'TBC', '', '{}', 'technelec.co.uk', '/assets/sponsors/technelec.png',  false, 80),
    ('imc',          'IMC',                       'Quantitative Trading', 'TBC', 'TBC', '', '{}', 'imc.com',         '/assets/sponsors/imc.svg',          false, 90),
    ('dca-design',   'DCA Design International',  'Product Design',       'TBC', 'TBC', '', '{}', 'dca-design.com',  '/assets/sponsors/dca-design.svg',   false, 100),
    ('goldman-sachs','Goldman Sachs',             'Investment Banking',   'TBC', 'TBC', '', '{}', 'goldmansachs.com','/assets/sponsors/goldman-sachs.svg',false, 110),
    ('apple',        'Apple',                     'Consumer Hardware',    'TBC', 'TBC', '', '{}', 'apple.com',       '/assets/sponsors/apple.svg',        false, 120)
ON CONFLICT (slug) DO NOTHING;
