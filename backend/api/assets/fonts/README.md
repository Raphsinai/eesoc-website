# Noto Color Emoji

The EESoc event photo-print renderer uses the bundled `NotoColorEmoji.ttf`
font for coloured emoji in event headings. This is required because Pillow's
normal text font does not render colour emoji, and the API image must behave
consistently regardless of system-installed fonts or network access.

Source: [googlefonts/noto-emoji](https://github.com/googlefonts/noto-emoji),
`2D/fonts/NotoColorEmoji.ttf` (font version 2.057, September 2026).

Copyright Google LLC. Distributed under the SIL Open Font License 1.1;
see `LICENSE` in this directory.
