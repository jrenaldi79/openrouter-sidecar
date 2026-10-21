---
title: "src/openrouter_image_mcp/hosted/admin/public/fonts"
description: "The self-hosted admin typefaces (Archivo SemiCondensed and JetBrains Mono as woff2) with their SIL OFL licences, copied from mpd-class-mcp and served at /admin/public/fonts."
type: "guidance"
status: "maintained"
read_when: "Adding, upgrading or removing an admin typeface."
---

**Source:** `mpd-class-mcp` commit `7e10e3664ce30d3b96e704bfc4e9242c9a76c384`, `src/admin/public/fonts/`. **Change it in `mpd-class-mcp` first**, then copy it again here.

**Owns:** the woff2 files that `../fonts.css` declares with `@font-face`, and their licences. `ArchivoSemiCondensed-{Regular,Medium,SemiBold,Bold,ExtraBold}.woff2` come from Omnibus-Type/Archivo (`OFL-Archivo.txt`). `JetBrainsMono-{Regular,SemiBold,Bold}.woff2` come from the JetBrains/JetBrainsMono 2.304 release (`OFL-JetBrainsMono.txt`). Both are unchanged from the source.

**Invariants:**

- Served without a session only for `fonts/<name>.woff2` (`views.STATIC_FILES`). The licences and this note are never served, but they ship in the package beside the fonts: the OFL requires the licence to travel with them.
- The CSP's `font-src 'self'` is what lets them load; never add a font CDN.
- Keep each licence next to the fonts it covers. A new face needs its woff2 here, an `@font-face` rule in `../fonts.css` and its licence; file names must match `[\w-]+\.woff2`.
