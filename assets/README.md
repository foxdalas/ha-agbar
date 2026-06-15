# Brand assets

Sources: `icon.svg` (light) and `dark_icon.svg` (brighter, for dark themes).
Rendered PNGs (256×256 + 512×512, trimmed, transparent, interlaced):

| File | Size | Purpose |
|---|---|---|
| `icon.png` | 256×256 | integration icon |
| `icon@2x.png` | 512×512 | hDPI icon |
| `dark_icon.png` | 256×256 | dark-theme icon (optional) |
| `dark_icon@2x.png` | 512×512 | dark-theme hDPI (optional) |

Regenerate:

```sh
gen() { rsvg-convert -w "$3" -h "$3" "$1" \
  | magick - -trim +repage -background none -gravity center -extent "$3x$3" \
      -strip -interlace PNG -define png:compression-level=9 "$2"; }
gen icon.svg      icon.png         256
gen icon.svg      icon@2x.png      512
gen dark_icon.svg dark_icon.png    256
gen dark_icon.svg dark_icon@2x.png 512
```

## Entity icons (already active, no external step)

Per-entity MDI icons ship in `custom_components/agbar/icons.json`, keyed by
`translation_key`. They render as soon as the integration is installed.

## Integration logo (Settings → Devices & Services, and HACS)

The tile icon is served only from the **home-assistant/brands** repo, keyed by
the `agbar` domain — it can't live in the integration package. HACS also requires
the domain to be present in brands for default-store inclusion.

1. Fork https://github.com/home-assistant/brands
2. Copy these files to `custom_integrations/agbar/`:
   `icon.png`, `icon@2x.png`, and (optional) `dark_icon.png`, `dark_icon@2x.png`
3. Open a PR. Once merged, HA/HACS fetch `https://brands.home-assistant.io/agbar/icon.png`.

Notes from the brands spec: PNG, transparency preferred, trimmed of empty edges,
lossless-optimized, interlaced. `dark_` variants are **optional** — if absent, the
normal icon is served. No symlinks or Home-Assistant-branded imagery in
`custom_integrations/`.
