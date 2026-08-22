---
name: QuantPilot
description: Cyberpunk glitch terminal for stock-move investigations — void ground, acid neon, chamfered HUD.
colors:
  letterbox: "#0a0a0f"
  panel: "#12121a"
  hud: "#2a2a3a"
  hud-deep: "#1c1c2e"
  hud-line: "#2a2a3a"
  osd: "#e0e0e0"
  readout: "#6b7280"
  caution: "#00ff88"
  caution-deep: "#00c46a"
  magenta: "#ff00ff"
  cyan: "#00d4ff"
  up: "#00ff88"
  down: "#ff3366"
typography:
  display:
    fontFamily: "Orbitron, Share Tech Mono, ui-monospace, monospace"
    fontSize: "clamp(2.25rem, 8vw, 6rem)"
    fontWeight: 900
    lineHeight: 0.9
    letterSpacing: "0.18em"
  headline:
    fontFamily: "Orbitron, ui-monospace, monospace"
    fontSize: "1.25rem"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "0.08em"
  title:
    fontFamily: "JetBrains Mono, ui-monospace, monospace"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: 1.6
    letterSpacing: "0.04em"
  body:
    fontFamily: "JetBrains Mono, ui-monospace, monospace"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: 1.6
    letterSpacing: "0.04em"
  label:
    fontFamily: "Share Tech Mono, ui-monospace, monospace"
    fontSize: "0.6875rem"
    fontWeight: 400
    lineHeight: 1.3
    letterSpacing: "0.2em"
rounded:
  sm: "2px"
  md: "4px"
  lg: "0px"
  xl: "0px"
  2xl: "0px"
  full: "0px"
spacing:
  gutter: "16px"
  gutter-lg: "32px"
  panel: "20px"
  rail: "64px"
  max: "1600px"
components:
  button-primary:
    backgroundColor: "{colors.caution}"
    textColor: "{colors.letterbox}"
    typography: "{typography.headline}"
    rounded: "{rounded.full}"
    padding: "10px 16px"
    height: "44px"
  button-primary-hover:
    backgroundColor: "{colors.osd}"
    textColor: "{colors.letterbox}"
    rounded: "{rounded.full}"
  button-secondary:
    backgroundColor: "{colors.hud-deep}"
    textColor: "{colors.osd}"
    typography: "{typography.headline}"
    rounded: "{rounded.full}"
    padding: "10px 16px"
    height: "44px"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.readout}"
    typography: "{typography.headline}"
    rounded: "{rounded.full}"
    padding: "6px 10px"
    height: "36px"
  input-ticker:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.osd}"
    typography: "{typography.headline}"
    rounded: "{rounded.full}"
    padding: "8px 16px"
  card-raised:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.osd}"
    rounded: "{rounded.lg}"
    padding: "16px 20px"
  card-raised-lg:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.osd}"
    rounded: "{rounded.xl}"
    padding: "16px 20px"
  chip-period:
    backgroundColor: "{colors.hud-deep}"
    textColor: "{colors.readout}"
    typography: "{typography.headline}"
    rounded: "{rounded.full}"
    padding: "8px 12px"
    height: "36px"
  chip-period-active:
    backgroundColor: "{colors.caution}"
    textColor: "{colors.letterbox}"
    typography: "{typography.headline}"
    rounded: "{rounded.full}"
    padding: "8px 12px"
    height: "36px"
  stamp:
    backgroundColor: "rgba(255, 80, 0, 0.15)"
    textColor: "{colors.down}"
    rounded: "{rounded.full}"
    padding: "2px 8px"
  stamp-quiet:
    backgroundColor: "{colors.hud}"
    textColor: "{colors.readout}"
    rounded: "{rounded.full}"
    padding: "2px 8px"
  readout-chip:
    backgroundColor: "{colors.hud-deep}"
    textColor: "{colors.osd}"
    typography: "{typography.label}"
    rounded: "{rounded.full}"
    padding: "4px 10px"
---

# Design System: QuantPilot

## Overview

**Creative North Star: "Hacked Feed"**

QuantPilot is a cyberpunk investigation terminal. The page is a void. Acid green, magenta, and cyan illuminate chamfered HUD panels. The signed move lives in a holographic quote well with a clipped Vanta net. Ranked claims, receipts, and position sit as chamfered cards beneath it. Scanlines, chromatic glitch, and a blinking cursor are part of the interface, not decoration layered on a brokerage dashboard.

Newsprint paper, cream serifs, Robinhood pills, and the retired Viewfinder amber HUD are out of world.

**Key Characteristics:**
- Void black `#0a0a0f` with a 50px circuit grid and a full-page scanline overlay
- Acid `#00ff88` for primary actions, focus, gains, and the net; magenta and cyan for secondary signal; `#ff3366` for loss, demotion, and errors
- Orbitron banners, JetBrains Mono body, Share Tech Mono labels
- Chamfered clip-path panels; neon stacked glows; no rounded pills
- Quote HUD first, then three evidence cards, then the rest of the desk

## Colors

A void with three neon instruments. Green is power. Magenta and cyan are interference. Destructive pink is failure.

### Primary
- **Acid green**: primary CTA, focus ring, Vanta net, gains, live lamps, and the look-back slider. Hover brightens and adds neon glow.

### Neutral
- **Letterbox**: page shell, rail, header, and the veil over the quote net.
- **Panel**: raised card fill — every case column, the quote well, and the tools pane.
- **HUD**: 1px borders and hairline dividers.
- **HUD Deep**: inset wells — period pills at rest, readout chips, company-mark tiles, weight-bar tracks.
- **HUD Line**: scrollbar thumb and inactive footer ticks.
- **OSD**: primary text and wordmarks.
- **Readout**: secondary text, labels, idle controls.

### Secondary
- **Up**: signed gains on the move figure and holdings change. Never chrome.
- **Down**: signed losses, the unverified hairline, Demoted stamps, and alert banners. Never chrome.

### Named Rules
**The One Blue Rule.** Caution blue is the only brand accent. Do not introduce a second marketing hue — not amber, not violet, not cyan. Its job is action, focus, and the lead claim.

**The Signed-Move Rule.** Green and red appear only on signed P/L and on trust marks (unverified hairline, Demoted stamp, failed-case alert). They do not color navigation, cards, or primary buttons.

## Typography

**Display Font:** Inter (with ui-sans-serif, system-ui, sans-serif)
**Body Font:** Inter (same stack)
**Label/Mono Font:** none in use — figures stay Inter with tabular numerals

**Character:** One retail UI face. Weight and size carry hierarchy; a second family is not part of the shipped system.

### Hierarchy
- **Display** (700, `clamp(2.25rem, 8vw, 5.5rem)`, line-height 0.92, tracking −0.03em): the signed move on the quote card. Color follows the sign (up / down).
- **Headline** (600, 0.9375rem, tracking −0.01em): column titles, primary button labels, wordmarks at 16px extra-bold.
- **Title** (400, 0.8125rem, leading relaxed): claim statements and receipt headlines.
- **Body** (400, 0.9375rem, leading 1.55): briefs, empty states, chat, alerts.
- **Label** (500, 0.6875rem, tracking 0.02em): meta, tallies, source tags. Sentence case, not a tracked uppercase tape.

### Named Rules
**The One Face Rule.** Inter is the only type family. Do not pair a squared HUD display or a mono body. Figures use `font-variant-numeric: tabular-nums` so columns align.

## Layout

A 64px icon rail is fixed on the left from the `lg` breakpoint up; the page offsets `64px` to clear it. Below that, the rail hides and the desk is full-bleed.

The command bar and main column share a `1600px` max width, with `16px` gutters that open to `32px` from `sm`. Main vertical rhythm is `20px` / `24px`. Cards pad `16px`, opening to `20px` from `sm`, and sit on a `16px` gap grid.

The case is one column on small screens, two from `lg`, and three from `xl` on `1.1fr / 1.15fr / 0.8fr` (What caused it / Receipts / Your position + How to read this). The quote card sits above that grid, full measure. Chat, case history, and the tools dock stack below.

The header is opaque letterbox. It sticks from `sm` up; on a phone it is static so a wrapping search row cannot eat the quote.

## Elevation & Depth

Depth is tonal plus one soft offset shadow. Cards lift off letterbox; they do not glow, glass, or cast hard drops.

### Shadow Vocabulary
- **Card** (`box-shadow: 0 8px 28px -10px rgb(0 0 0 / 0.55)`): default lift on every raised card and on the quote well.
- **Quiet** (`box-shadow: none`): empty/ghost tiles (idle tool tabs, watchlist chips). Emphasis comes from a caution border on hover or active, not extra lift.

### Named Rules
**The Soft Card Rule.** If a surface is a card, it uses the card shadow at rest. Do not add a second glow, a hard offset, or a backdrop-filter as the elevation language.

## Shapes

Closed rounded rectangles. Default cards are gently curved (16px). Large case cards and the lead-claim nest use 20px. Company-mark tiles and alert banners use 24px. Buttons, period pills, stamps, readout chips, and the ticker field are full pills.

Borders are 1px HUD at rest. Active or hovered snap targets shift that border to caution blue. The quote well is a clipped 24px-class rounded rectangle (`overflow: hidden`) so the net cannot escape.

### Named Rules
**The Closed Card Rule.** Containers are continuous rounded boxes. Open L-corner brackets, zero-radius plates, and hairline-only frames are the retired HUD and are out of world.

## Components

### Buttons
- **Shape:** full pill (`9999px`), 44px minimum height, 10px 16px padding, 14px semibold Inter.
- **Primary:** caution fill, letterbox label. Hover / focus-visible fills OSD. Disabled at 45% opacity.
- **Secondary:** HUD-deep fill, 1px HUD border, OSD label. Hover / focus-visible border goes caution.
- **Ghost:** no fill. Readout label; HUD-deep well and OSD text on hover. Used for header Position / Sources / Sign out.

### Chips
- **Period pills:** look-back radios. Idle is a HUD-deep pill in readout; the selected pill fills caution with a letterbox label.
- **Stamps:** 10px semibold pills. Demoted / risk-high use down on a 15% down wash. Example / Unverified / Unlinked use HUD fill and readout text.
- **Readout chips:** HUD-deep pills for status. A live chip may blink a 6px caution lamp (`rec-blink`, 1.6s steps).

### Cards / Containers
- **Corner Style:** 16px default, 20px for large case cards.
- **Background:** panel, 1px HUD border.
- **Shadow Strategy:** the card shadow; quiet variants drop the shadow and the fill.
- **Internal Padding:** 16px / 20px.
- **Lead claim:** the surviving #1 explanation is itself a nested 20px card with a caution border. Runners-up are hairline rows, not a stack of identical boxes.

### Inputs / Fields
- **Ticker field:** pill-shaped panel well, 16px semibold uppercase OSD value, readout placeholder in sentence case. Focus / hover rings the border caution. `⌘K` hint sits in a HUD-deep chip on large screens.
- **Focus (global):** 2px caution outline, 3px offset.
- **Selection:** caution wash, letterbox text.

### Navigation
- **Rail:** 64px letterbox column, HUD right border. Wordmark is a 14px extra-bold “Q”. Hit targets are 44×44 at 12px radius; readout at rest, HUD-deep well on hover.
- **Tools dock:** six quiet tiles on small screens (three-up), six-up from `lg`. Active tile gains a caution border and caution label.

### Quote card
The signature surface. A clipped rounded well (`panel` fill, card shadow) holding a Vanta net (`#4F7BFF` on `#0B0D12`, 9 points, spacing 18). A letterbox gradient veil (`from letterbox via 90% to 55%`) keeps the figure readable. The signed move is display Inter, green or red. Period pills and explanation/receipt tallies sit on the right from `lg`. The net is never tiled behind the page.

### Weight bar
A 6px rounded track in HUD-deep. The lead fill is caution; others are readout at 70%. It plays `bar-fill` once (0.7s, cubic-bezier(0.16, 1, 0.3, 1)). The percent is a 12px tabular readout, caution when it is the lead.

### Unverified mark
A 4px full-pill down bar on the top edge of a claim that has no linked evidence. Unlinked receipts do not get this mark — they get a quiet stamp only.

### Company mark
A 24px-radius HUD-deep tile. Prefer the issuer logo; if it fails, a two-letter extra-bold monogram in readout. Sizes: 24px / 32px / 56–80px.

## Do's and Don'ts

### Do:
- **Do** put the quote first and the three evidence cards directly under it.
- **Do** keep caution blue rare — primary CTA, active period, lead rank, focus, Vanta net.
- **Do** color the signed move and holdings change with up / down, and nowhere else for decoration.
- **Do** clip any WebGL field inside the quote card.
- **Do** halt Lenis, freeze the net (`maxDistance: 0`), and skip GSAP counts when `prefers-reduced-motion` is set. Content is visible at rest.

### Don't:
- **Don't** revive Viewfinder chrome: open L-brackets, caution amber `#FFB000`, 45° zebra crawl, Oxanium, Spline Sans Mono, or a flat zero-radius plate.
- **Don't** tile a net, fog, or gradient as page wallpaper.
- **Don't** use green or red on navigation, card chrome, or primary buttons.
- **Don't** introduce violet, purple, or cyan AI-dashboard accents — leftover `.glow-violet` / `.gradient-text` classes are already neutralized to none / solid caution.
- **Don't** ship a second type family for figures. Tabular Inter is the readout.
