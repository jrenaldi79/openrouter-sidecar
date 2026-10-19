---
title: "ui/src/assets"
description: "Static assets inlined into the MCP App bundles: provider logo SVGs (with their license) and the product-original/edited/mask demo images used by onboarding."
type: "guidance"
status: "maintained"
read_when: "Changing or adding files in ui/src/assets/."
---

**Owns:** `provider-*.svg` (license in `PROVIDER-LOGOS-LICENSE.txt`, provenance in `README.md`) and the `product-*.webp` demo images that `ui/build.mjs` inlines.

**Invariants:** keep assets small, because each one is inlined into every bundle that imports it. New third-party art needs its license recorded here.
