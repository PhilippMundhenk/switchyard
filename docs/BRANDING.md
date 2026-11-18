# Branding

Product name **Switchyard**, code name `switchyard` (package, CLI, image, `SWITCHYARD_` prefix).
Plain, calm, tool-like. No marketing.

Disclaimer, used in the README, the web footer and here: *Switchyard is an independent
open-source project. It is not affiliated with, endorsed or supported by NETGEAR. Product names
are used only to say what it works with.*

## Tokens

Defined as CSS custom properties in `web/static/style.css`.

| Token | Light | Dark |
| --- | --- | --- |
| `--bg` | `#f4f6fa` | `#0f1522` |
| `--surface` | `#ffffff` | `#182032` |
| `--text` | `#17202e` | `#e6ebf5` |
| `--muted` | `#5d6a82` | `#96a3bd` |
| `--accent` | `#2f6fed` | `#5b92ff` |
| `--ok` / `--warn` / `--danger` | `#1f9d55` / `#b7791f` / `#d64545` | `#3ecf8e` / `#e3b341` / `#ff6b6b` |
| link speed 10M / 100M / 1G / 10G | `#9aa7bd` / `#e0a21a` / `#22a06b` / `#7c5cf0` | `#6c7a93` / `#e3b341` / `#3ecf8e` / `#a28bff` |

Type: the system font stack; monospace for MAC addresses and the protocol log. Radius 10 px.

## Components

Cards with a thin border; one primary button per view; ports drawn as small jacks coloured by
link speed; the VLAN matrix uses `U` (untagged), `T` (tagged) and `–` (not a member).

## Tone

Short sentences, plain words, say what will happen before a destructive action.

## Icon

A railway junction: one track that splits into three, ending in dots. It stands for a switch
(points) and for ports. White strokes and dots on a rounded square in the accent blue `#2f6fed`,
64 x 64 viewBox, readable at 16 px. Source: `docs/assets/icon.svg`. `python tools/make_icons.py`
derives `favicon.svg`, `apple-touch-icon.png`, `icon-192.png`, `icon-512.png`, `site.webmanifest`
(web static folder) and `docs/assets/social-preview.png` (1280 x 640, upload it in the repository
*Settings*, *Social preview*). The web interface shows the same SVG as its brand mark.
