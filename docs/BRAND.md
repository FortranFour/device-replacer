# Product family icon style

Device Replacer uses the approved **B · Connection handoff** design: a dashed original device/entity, a curved arrow, and a solid replacement. The mark is a vector implementation of that concept, with balanced spacing for small integration tiles.

| Element | Specification |
| --- | --- |
| Primary color | Cyan `#00BFFF`, shared with the Power Fingerprint design direction |
| Shapes | Rounded outlines, open interiors, and simple functional symbols |
| Source icon | `custom_components/entity_replacer/frontend/icon.svg` |
| Stroke | 8 units on a 256 × 256 viewBox; rounded caps and joins |
| Icon background | Transparent; no containing badge or shadow |
| Light wordmark | Charcoal `#202A35` |
| Dark wordmark | White `#FFFFFF` |
| Regular icon | 256 × 256 RGBA PNG |
| Retina icon | 512 × 512 RGBA PNG |
| Regular logo | 1200 × 256 RGBA PNG |
| Retina logo | 2400 × 512 RGBA PNG |

The integration's `brand/` directory contains all eight supported light/dark, icon/logo, and regular/retina PNG variants. The cyan icon is the same in light and dark themes; the wordmark color changes. Home Assistant 2026.3+ serves these local images for the integration. The panel header uses the SVG. The native sidebar retains its standard Material Design navigation icon.

The SVG wordmarks in this directory are editable source files. Fonts are outlined in the distributed SVG wordmarks so rendering does not depend on installed fonts.

For future products, reuse the cyan, stroke weight, rounded geometry, and transparent background while designing a symbol for each product's function. This repository includes only Device Replacer’s production assets.

All included brand artwork is distributed under the repository's MIT license.
