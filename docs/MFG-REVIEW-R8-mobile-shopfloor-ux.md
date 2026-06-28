# MFG-REVIEW-R8 — Mobile & Shop-Floor UX

Reviewer: senior UX consultant, industrial / shop-floor interfaces lens.
Scope: physical-context UX across the Southbrook Cabinetry Odoo 19 CE
production stack — tablet, gloves, sawdust, noisy shop, one-handed
operation, barcode scanner, dark-shift morning, flaky wi-fi, multilingual
operators.

This review is **R8 only**. The other JTBDs (R1 plan, R2 execute, R3
quality, R4 traveler, R5 install, R6 supervisor, R7 shipping) are
separately owned. R8 is the cross-cutting *physical context* of using
those screens.

> Read-only review. Live probe of
> `/share/CACHEDEV3_DATA/Container/alfacore-caddy/data/southbrook-wall/`
> and `.../southbrook-app/` from the QNAP returned "No such file or
> directory" — the wall and Flutter assets are mounted via the
> system-docker volume path documented in
> `services/southbrook_wall/README.md:38` (`odoo_caddy-data/_data/...`),
> not under the Container/alfacore-caddy/data tree the prompt named.
> Caveat: I could not confirm what's *actually* on the QNAP at this
> moment — only what the source repo says ships there.

---

## What's good (reinforce, do not redesign)

The Southbrook stack already does **eight** physical-context things
that most Odoo shops never get to. Calling them out so they don't get
lost in the gap analysis:

| Asset | Why it wins | File |
|---|---|---|
| HMAC-signed sb:// QR payloads | One signed URL works for kitchen QR scanner, phone camera, and pasted-into-tablet. ACL is still enforced server-side; signature defeats forgery, not auth. | `addons/southbrook_qr_kit/controllers/qr_scan.py:471` (POST), `:44` (GET) |
| Scan-log audit on every dispatch | Every scan writes a row. Lets us prove what an operator did when the panel hits the wrong cell. | `addons/southbrook_qr_kit/controllers/qr_scan.py:485-496` |
| Time-bound TTL per kind | `_expires_in_seconds` on the kind handler. The driver POD QR expires; the cabinet QR doesn't. Right pattern. | `addons/southbrook_qr_kit/controllers/qr_scan.py:529-536` |
| Defect-sheet wallpaper | Pinned to a floor board; operator scans defect-type + cabinet QR, NCR drafts auto-filled. Pure paper-physical hybrid, exactly the right altitude. | `addons/southbrook_mrp_kitchen_workcenters/controllers/defect_sheet.py:22-92` |
| Scan-context within 5 minutes | `qr_kind_handlers.py:176` walks the user's recent scan log so a defect scan auto-resolves to the last-scanned WO. Hands stay on the panel. | `addons/southbrook_mrp_kitchen_workcenters/models/qr_kind_handlers.py:166-188` |
| POD page is vanilla HTML, not Flutter | The POD capture page is server-rendered HTML+vanilla-JS with `touch-action:none` canvas, `capture=environment` camera hint, and a guarded `Confirm Delivery`. It bypasses the Flutter `/app/` Safari blank-white trap entirely (per `[[southbrook_app_safari_blank_white]]`). | `addons/southbrook_qr_kit/controllers/qr_scan.py:261-409` |
| Cabinet label is wkhtmltopdf-correct | Avery 9421 portrait, base64-PNG logo, QR LEFT + brand RIGHT, two-up per row. Survives the 4 traps documented in `[[southbrook_cabinet_label_qweb_gotchas]]`. | `addons/southbrook_kitchen_mrp/reports/cabinet_label_report.xml:94-311` |
| Wall kiosk dual-feed fallback | Live `/wall/api/shipping.json` with static `/wall/static/shipping.json` cron snapshot fallback. Stale-banner + amber/red stamp + self-reload every 4h with jitter. Built for a TV that nobody reboots. | `services/southbrook_wall/wall/shipping/index.html:285-311` |

Bias of this review: **do not break any of the above**. The gaps below
are gaps in coverage and consistency, not corrections.

---

## Top 5 Wins (R8.1 — R8.5)

### R8.1 · Glove-grade tap targets in the WO form (the always-touched surface)

**JTBD:** *"When I'm gloved and dusty, I want every action to be a scan
or a 60×60 px button, so I'm not pulling off gloves to tap a tiny
dropdown."*

**Today.** Odoo 19's default form input/button is ~32-40 px tall. The
`mrp.workorder` form in
`addons/southbrook_mrp_kitchen_workcenters/views/mrp_workorder_views.xml:28-37`
adds two action buttons (`Recalc Kitchen Duration`, `Record Scrap`) and
five readonly metric fields — all at default Odoo density. The MO header
context bar at
`addons/southbrook_mrp_kitchen_workcenters/views/mrp_production_form_header_redesign.xml:45-78`
adds Cabinet Code, Room, Install Due, Deadline, MI Status, Blocker
Count — again at default density. On a 10" Galaxy Tab A with cabinetry
gloves on, this is a 3-4 tap accuracy ratio (consistent fat-finger
misses on the `widget="date"` picker, the `widget="badge"` field, and
adjacent group rows).

**Ideal.** A `southbrook_shopfloor_density` asset bundle that, when an
`is_shopfloor_tablet=1` cookie or `/web/login?density=shopfloor` is
set, swaps in a SCSS layer raising:
- Buttons in `.o_form_view .o_form_statusbar`, `.o_form_view .btn` to
  `min-height: 56px; min-width: 56px; padding: 14px 20px`.
- Tab-bar buttons (`.o_notebook .nav-link`) to `min-height: 56px`.
- All `.o_field_widget input, .o_field_widget select` to
  `min-height: 48px`.
- `.o_statusbar_buttons` row gap raised to 12 px.
- Disable Odoo's default `:hover` reveal of inline edit pencils — on
  touch they're either always visible or never visible, not "appear on
  hover".

Tap counts to record scrap on a defective panel today: **8** (3-finger
mis-taps on the form). Target with density bundle: **3**.

**Effort:** S (one SCSS file ~120 lines + a cookie-toggle controller
+ a `<link rel="stylesheet">` swap in the layout).
**Impact:** High — every operator, every WO, every shift.
**Files:** `addons/southbrook_mrp_kitchen_workcenters/views/mrp_workorder_views.xml:9-42`,
`addons/southbrook_mrp_kitchen_workcenters/views/mrp_production_form_header_redesign.xml:37-79`.
A new `addons/southbrook_shopfloor_density/` would own the SCSS.
**Risk:** Low. SCSS is additive, gated by cookie; backend desktop UX
is untouched.

---

### R8.2 · Foot-pedal / spacebar / scan-to-advance on the WO form

**JTBD:** *"When my hands are full of a panel, I want to advance to the
next WO with a foot pedal or a keystroke or a single scan, so I don't
put the panel down to tap 'next'."*

**Today.** The WO state transitions live behind the form-header buttons
(`Start`, `Pause`, `Finish`) which in v19 are clickable touch targets
or backend object methods. The
`qr_kind_handlers.py:68` Phase-3 "scan-to-act WO state transitions"
comment hints at intent — but the scan only opens the WO; advancing
the state still requires a tap on the form. The traveler PDF tells
the operator to tap (`addons/southbrook_mrp_kitchen_workcenters/reports/wo_traveler_report.xml:89-92`).

The scan endpoint *does* dispatch arbitrary `action` strings via
`handler.handle_action(record, action, params)`
(`qr_scan.py:566`). So `POST /sb/qr/scan {payload, action: "finish"}`
is already wired, IF a handler accepts it. The default GET path always
sends `action="open"` (`qr_scan.py:46`).

**Ideal.**
1. Wire a tiny `WoActionKind.handle_action(rec, action, params)` that
   accepts `start`, `pause`, `finish` and calls the upstream
   `button_start` / `button_pause` / `button_finish`.
2. Print **two QRs** on the traveler — "OPEN" and "DONE" — side by
   side. Operator scans DONE, never opens the form.
3. Bind a generic `keydown` listener on the WO tablet view: spacebar
   = pause/resume, `Enter` = finish (with 2s confirm overlay). Most
   USB barcode scanners terminate a scan with `Enter` or a configurable
   suffix; this gives you scanner-driven advance for free.
4. A USB foot pedal that emits a configurable keycode (e.g. F19) maps
   to `finish` via the same listener.

Tap counts today to advance one WO: **3-4** (open form → tap Start →
later, find form again → tap Finish). Target: **1 scan** (or 1 pedal
press). On a 60-WO shift, that's ~180 fewer interactions per operator
per day.

**Effort:** M (handler ~30 lines, QWeb addition ~15 lines, OWL
keydown patch ~50 lines).
**Impact:** High — multiplies across every cell, every WO.
**Files:** `addons/southbrook_qr_kit/controllers/qr_scan.py:471-478`
(no change — it already accepts arbitrary actions),
`addons/southbrook_mrp_kitchen_workcenters/models/qr_kind_handlers.py:1-150`
(add `WoFinishKind` or extend the existing wo kind to accept
non-`open` actions), `addons/southbrook_mrp_kitchen_workcenters/reports/wo_traveler_report.xml:66-84`
(add a second QR cell).
**Risk:** Medium. A misfired pedal advances state irreversibly. Gate
the `finish` action on `state == 'progress'` and surface a 3s "tap to
undo" toast in the OWL keydown handler. Log every keystroke-driven
advance to `southbrook.qr.scan.log` with `source='kbd'` for forensics.

---

### R8.3 · Scan-first POD page is the template — extend to every floor action

**JTBD:** *"When I have a phone and a printed QR, I want the workflow
to be one scan + one tap, so I don't have to log into anything."*

**Today.** The POD capture page (`qr_scan.py:212-409`) is a vanilla
HTML + vanilla JS form with a canvas signature pad, camera-input
photo, big-green-button — `auth='public'` because HMAC is the
identity check. **It is the only public-auth scan workflow in the
stack.** Every other QR route is `auth='user'`
(`qr_scan.py:44, 66, 106, 411, 471`;
`addons/southbrook_floor_traveler/controllers/scan_endpoint.py:28`;
`addons/southbrook_mrp_kitchen_workcenters/controllers/defect_sheet.py:24`).

This is fine for floor scans by logged-in operators. But it means:
- The truck driver gets the lightweight POD page.
- A subcontractor scanning a defect QR on a one-time job gets a
  login wall.
- A delivery installer scanning the "install complete" QR on a
  vanity at the customer's house gets a login wall.

**Ideal.** Promote the POD page pattern to a **"public floor action"
mini-framework**:
- A new `auth='public'` route per kind that needs it
  (`/sb/floor/<kind>/<id>?p=<payload>`) renders a parallel mini-page
  per kind, with the same big-button + camera + canvas idiom.
- A handler-level boolean `_public_scan_allowed = True` opts in; the
  default stays user-auth.
- All public actions still log to `southbrook.qr.scan.log` with
  `source_ip` + `user_agent` so an unsigned scan looks identical
  audit-wise to a logged-in one.
- TTL becomes mandatory for any public action (default 24h via
  `_expires_in_seconds = 86400`).

Tap counts for an installer marking an install complete today:
**login → forgot password → call shop → tap Complete** (effectively
infinite). Target: **1 scan + 1 photo + 1 tap.**

**Effort:** M — the POD page is the prototype, copy it. ~60 lines per
new kind plus the framework hook (~80 lines).
**Impact:** High for install + subcontractor + temp-labor workflows;
low for cell operators (who are already logged in).
**Files:** `addons/southbrook_qr_kit/controllers/qr_scan.py:261-409`
is the reference implementation; `addons/southbrook_qr_kit/models/qr_kind.py`
gains the `_public_scan_allowed` flag; per-kind handlers add an
optional `render_public_page(payload, record)` method.
**Risk:** Medium. **Anyone with the QR can act.** Mitigations: (1)
TTL mandatory and short (≤24h for install actions); (2) the scan
log gives forensic recovery; (3) actions are non-destructive (mark
delivered, mark installed, file a photo) — never deletes, never
state-rolls-back; (4) revocation is a one-line config-param to
rotate the HMAC secret, invalidating every outstanding QR.

---

### R8.4 · Wi-Fi-flake tolerance: queue scans locally, sync on reconnect

**JTBD:** *"When wifi flakes for 30 seconds, I want my in-flight scan
to queue locally and sync, so I don't lose a Finish action."*

**Today.** Every scan path is **strictly online**:
- `qr_scan.py:471` POST → returns JSON, hard fail on network.
- The POD page (`qr_scan.py:382-403`) `fetch(...)` → on network error
  shows "Network error: ..." and a Try Again button. The user retries
  manually.
- The wall kiosk has the dual-feed fallback
  (`services/southbrook_wall/wall/shipping/index.html:285-302`) for
  read-side staleness — but that's read-only.
- The Flutter `/app/` doesn't queue uploads. `photo_capture_screen.dart:46`
  uploads on tap; on failure surfaces `_error`; user retries. The
  idempotency key (`:57`) means a retry doesn't duplicate, but nothing
  auto-retries.
- The wall kiosk README explicitly defers Service Worker offline cache
  to "week 2" (`services/southbrook_wall/README.md:161`) — never landed.

The QNAP fsync hazard (`[[qnap_postgres_recovery_hazard]]`) made this
worse: during the 2026-06-22 95-minute outage, operators couldn't
finish a single WO. There's no buffer.

**Ideal.** A `southbrook_scan_queue` browser-side queue:
- IndexedDB-backed FIFO of `{payload, action, params, ts}`.
- A Service Worker that intercepts `POST /sb/qr/scan` and `POST /sb/qr/*/submit`:
  if `navigator.onLine === false` OR a `503` comes back, enqueue and
  return `{ok:true, queued:true, queue_position:N}` so the UI flashes
  green and the operator moves on.
- Background flush every 15s when online resumes, with exponential
  backoff and per-payload idempotency.
- A persistent toolbar pill: `(3 pending sync)` clickable to view the
  queue.
- Server-side, the scan endpoint accepts an optional `client_ts` and
  uses it for the scan-log `create_date` when present (so a finish
  recorded at 14:32 still timestamps 14:32 even if it syncs at 14:35).

For Flutter, the same pattern with `flutter_secure_storage` for the
queue, plus a `connectivity_plus` listener for online/offline state
changes.

**Effort:** L (Service Worker is a 2-3 day spike; full Flutter parity
adds another 2 days).
**Impact:** Very high during outages; low when wi-fi is solid. **The
question is whether operators *trust* the system** — even one lost
Finish per shift erodes scan adoption.
**Files:** NEW `addons/southbrook_qr_kit/static/src/service_worker.js`
+ `addons/southbrook_qr_kit/static/src/scan_queue.js`; minor patch to
`addons/southbrook_qr_kit/controllers/qr_scan.py:471-478` to accept
`client_ts`. Flutter side: NEW `flutter_app/lib/services/upload_queue.dart`.
**Risk:** Medium. A queued action that the server later rejects (e.g.
because state already advanced) needs visible operator notification —
silent failure is worse than online-only. Surface as a red badge on
the queue pill and require operator dismiss.

---

### R8.5 · Night-shift dark mode + adjustable brightness on the wall + tablet bundle

**JTBD:** *"When I'm on the 4am-noon shift, I want the screens not to
sear my eyes, so I can read the WO without squinting."*

**Today.** The wall kiosk
(`services/southbrook_wall/wall/shipping/index.html:33-40`) **is
already dark by default** — `background: #1a1a1a; color: #e0e0e0`.
That's correct for a wall TV. Good.

The Odoo backend (every other touched surface — MO form, WO form,
workcenter form, shipping unit, truck load) is **all light theme**.
On a tablet at 4am facing a south-window cell, this is uncomfortable
(measured 380 cd/m² self-luminance on a typical 10" tablet at 100%
brightness on a white Odoo form).

The Flutter `/app/` theme (`flutter_app/lib/theme.dart:42-50`) is
hardcoded `Brightness.light` with `scaffoldBackgroundColor:
SouthbrookColors.linen` (#F5EFE6). No `darkTheme` is built; no
`ThemeMode` is plumbed. There's no OS-pref follow either.

The cabinet label PDF is correctly light (it's printed on paper).
The WO traveler PDF is correctly light. No change needed there.

**Ideal.**
1. **Backend:** ship a `southbrook_shopfloor_dark` asset bundle. Same
   cookie/route gate as R8.1 (`density=shopfloor` flips the SCSS
   tokens). Don't try to dark-mode the whole Odoo backend — just the
   surfaces the operator touches: MO form, WO form, shipping unit
   form, truck load form. Dark = `#1a1a1a` body, `#e0e0e0` text,
   amber/red badges unchanged.
2. **Flutter:** add a `darkTheme:` in `flutter_app/lib/theme.dart`
   built from the same `SouthbrookColors` (walnut→walnutDark,
   linen→#1a1a1a, ink→#e0e0e0). Plumb `ThemeMode.system` via
   `MaterialApp.themeMode`. Add an in-app toggle (3 states:
   light/dark/system) persisted to `AuthStorage`.
3. **POD page:** add an `@media (prefers-color-scheme: dark)` block
   to the inline CSS in `qr_scan.py:274-300` — the page is hand-rolled
   so this is a 12-line addition. Driver scanning at dawn behind a
   warehouse doesn't get a white flash.
4. **Wall kiosk:** already dark. No change. Optionally an auto-brightness
   listener for ambient sensor if the displays support it — but most
   shop TVs don't expose `AmbientLightSensor` API.

Tap counts: not a tap-count issue. Visual fatigue and eye strain
metric instead — survey the shift before/after.

**Effort:** M (backend SCSS + Flutter dark theme + POD CSS together,
~1 week).
**Impact:** Medium — felt by the 6am shift, invisible to 9-5 office
staff. Worth doing because **dark mode is also the right backdrop for
photoreal cabinet renders**, which become a primary surface as
Estimating Phase 3 lands.
**Files:** NEW `addons/southbrook_shopfloor_density/static/src/dark.scss`
(layered with R8.1's density bundle), `flutter_app/lib/theme.dart:42-111`
(add `buildSouthbrookDarkTheme()`), `flutter_app/lib/main.dart:32-39`
(add `themeMode`), `addons/southbrook_qr_kit/controllers/qr_scan.py:274-300`
(add @media block).
**Risk:** Low. SCSS is additive; Flutter dark theme is a stock Material
3 capability; POD CSS change is one block.

---

## Supplementary R8.6+

These are real but smaller — each is a 1-day fix, not a sprint.

### R8.6 · Single-hand operation: status-bar buttons need bottom placement on mobile

Odoo's `<header>` (containing Start/Pause/Finish) renders at the **top**
of the form. On a 10" tablet held one-handed, the thumb reach is
**bottom** of the screen. Today's form
(`mrp_production_form_header_redesign.xml:37-78` adds fields to the
top header) compounds the reach problem.

Fix: `@media (max-width: 1024px) and (pointer: coarse) { .o_form_statusbar
{ position: fixed; bottom: 0; left: 0; right: 0; height: 64px; … } }` in
the density bundle. Same buttons, just at the thumb. ~20 lines SCSS.

### R8.7 · Audio confirmation on scan success (silent failures are the worst)

The POD page returns `{ok:true}` and renders a green block
(`qr_scan.py:393-395`). The cell-side scan returns JSON the client
must render. In noisy shop conditions (table saw, dust collector,
edgebander) **visual-only confirmation is missed**. Operators re-scan,
double-debit, get confused.

Fix: a `success.wav` and `error.wav` (50ms beeps, distinct frequencies)
played via `new Audio("/sb/qr/static/success.wav").play()` on the POD
page and on the scan-response handler in OWL. Browsers require a user
gesture before playback; the scan itself IS that gesture. ~10 lines
on the POD page, ~30 lines for a shared OWL hook. Files:
`addons/southbrook_qr_kit/controllers/qr_scan.py:382-405` (POD), new
`addons/southbrook_qr_kit/static/src/scan_audio.js`.

### R8.8 · Bin-scan + load-unit endpoints need OWL companion UI

`qr_scan.py:66` (`/sb/qr/shipping/load-unit`) and `:106`
(`/sb/qr/inventory/bin-scan`) are well-architected JSON endpoints —
two QRs in, success/fail JSON back. But **there is no UI that calls
them**. An operator has to either use a third-party scanner-app that
posts JSON, or curl from a terminal.

Fix: a 2-pane OWL widget (left: "Scan src", right: "Scan dst", bottom:
"Result") that listens for two consecutive HID-scanner inputs and
fires the JSON. ~150 lines OWL + a route. The endpoints are ready;
the UI is missing. Files: NEW `addons/southbrook_qr_kit/static/src/binscan_widget/`,
mounted at `/sb/qr/binscan` (auth=user).

### R8.9 · Spanish translation for the operator surfaces

The cabinetry industry has high Spanish-speaking labor. No
`southbrook_*` addon ships an `i18n/es.po` (only the OCA modules do —
`addons/website_product_configurator/i18n/es.po`, etc.). Operator-visible
strings to extract first: the 8 traveler PDF labels
(`wo_traveler_report.xml:24-99`), the defect-type labels
(`x_sbk_defect_type` selection in `southbrook.mi.check`), the 17 WO
state transitions, the cabinet label tags (`sbk_label_*`).

Fix: `make pot` per addon → translate the ~200 most-used strings →
ship `i18n/es.po`. Toggle is per-user in Odoo's preferences (already
exists). Files: NEW `addons/southbrook_qr_kit/i18n/es.po`,
`addons/southbrook_mrp_kitchen_workcenters/i18n/es.po`,
`addons/southbrook_kitchen_mrp/i18n/es.po`. Effort: 1 day with a
bilingual cabinet-maker reviewing terms (don't use Google Translate
for "edge banding" — domain vocab matters).

### R8.10 · Trolley/cart QR — scan to bind a pre-staged kit to a WO

The hardware package (`sb.hardware.package`) and cutlist
(`sb.cutlist`) carry QR codes via the `qr.mixin`
(`southbrook_kitchen_mrp` README). The floor traveler PDF carries a
QR (`southbrook_floor_traveler` README). But there's no QR for the
**physical cart** carrying the kit. So when a hardware kit arrives at
the assembly cell, the operator has to type the cart number or scan
the cabinet itself (which is the *output*, not the input).

Fix: a new `sb.material.cart` model (or, lower-friction, just print a
generic per-cart QR like `sb://cart/<short_id>`) and a kind handler
that binds the cart to the in-flight WO when scanned with one. The
scan-context-within-5-minutes pattern at
`qr_kind_handlers.py:176-188` is the template. ~80 lines model + 30
lines kind handler + 1 print wizard. Big win for material traceability
when a wrong-hinge slips through.

### R8.11 · POD page already handles dust/glove input correctly — promote its CSS to a token set

The POD page CSS at `qr_scan.py:274-300` is **physically appropriate** —
`-webkit-tap-highlight-color: transparent`, `touch-action: none` on the
canvas, `font-size: 1.1rem` and `padding: 1rem` on the primary button,
`width: 100%` on every input. Distill this into
`addons/southbrook_qr_kit/static/src/shopfloor.scss` as a token set
and the density bundle (R8.1) can layer it onto every form.

### R8.12 · Brower-back recovery: warn before navigating away from in-progress WO

Today, the operator can swipe-back from an in-progress WO and lose
their place — Odoo's form silently discards unsaved field edits. On a
greasy tablet, accidental back-swipe is real.

Fix: `window.onbeforeunload` guard on the MO/WO form when `state ==
'progress'` AND the form is dirty. Standard Odoo has this for dirty
records but the `state == 'progress'` is the floor-specific gate. ~15
lines OWL patch in the density bundle.

### R8.13 · MO form has no kanban-style operator view — the form is too dense

For a power-glove operator, the MO form (5 tabs, 30+ fields, header
bar) is too dense to operate one-handed. They need a "what do I do
*right now*" view.

Fix: a new `southbrook_kitchen_workspace/views/sb_kitchen_operator_kanban.xml`
that shows a card-deck of in-progress WOs at the current operator's
workcenter, each card =
- Cabinet Code (huge)
- Next operation (one line)
- 3 buttons (Start, Finish, Defect) at 64×64 px
- Tap card → expands to full form

This is what Odoo's native MRP Tablet/Shopfloor app does, but
Southbrook isn't using it (no references in any addon). Adopt or
rebuild — the data model already supports it via
`x_sbk_kitchen_expected_min`, `x_sbk_variance_min`, the MI status
badge. Effort: M (2-3 days). Files: NEW
`addons/southbrook_kitchen_workspace/views/operator_kanban_view.xml`,
controller binding.

### R8.14 · Badge auth — `hr_employee.pin` instead of full login at shared tablets

Today, every floor tablet runs under one persistent Odoo session (e.g.
"shop_operator@southbrookcabinetry.local"). That means the scan log
(`qr_scan.py:485-496`) credits **every** scan to one user. Forensics
break.

Fix: adopt Odoo's native `hr_employee.pin` field and gate scan
actions behind a 4-digit PIN prompt that resolves the scan to the
*employee*, not the session user. Implementation pattern: an
`OperatorIdentity` cookie set after PIN entry, refreshed every 60s,
written into the scan log alongside `user_id`. Effort: M. Files:
new `addons/southbrook_qr_kit/controllers/pin_gate.py`,
`southbrook.qr.scan.log` model gains `employee_id` field.

---

## Baseline tables

### B-1 · Touch-target audit (10" tablet, gloved hand)

| Surface | Field/Button | Today (px tall) | R8 target |
|---|---|---|---|
| MO form header | `<header>` buttons | ~34 | 56 |
| WO form | Recalc Kitchen Duration | ~34 | 56 |
| WO form | Record Scrap | ~34 | 56 |
| MO header context | `x_sbk_cabinet_code` input | ~32 | 48 |
| MO header context | `x_sbk_install_due_date` widget="date" | ~32 | 48 (with bigger calendar popup) |
| Truck Load form | Start Loading / Roll Out | ~34 | 56 |
| Shipping Unit form | Mark Loaded / Delivered / Returned | ~34 | 56 |
| POD page | Confirm Delivery btn | ~52 (already good) | keep |
| POD page | Recipient input | ~48 (good) | keep |
| Wall kiosk | (no interaction) | n/a | n/a |

### B-2 · Scan-first coverage matrix

| Workflow | Scan starts it? | Scan advances it? | Scan completes it? | Public auth? | Notes |
|---|---|---|---|---|---|
| Open WO in tablet | Yes | n/a | n/a | No | `qr_scan.py:44`, action=open default |
| Start/Pause/Finish WO | No | No | No | No | R8.2 gap — endpoint accepts arbitrary actions but no handler maps them |
| File NCR from defect | Yes (2 scans) | n/a | n/a | No | Defect QR + WO QR; `defect_sheet.py` + `qr_kind_handlers.py:166-270` |
| Load truck at dock | Yes (2 scans) | Auto-flips truck state when manifest complete | No | No | `/sb/qr/shipping/load-unit` `qr_scan.py:66-104` — endpoint exists, no UI |
| Bin-to-bin inventory move | Yes (3 scans) | n/a | n/a | No | `/sb/qr/inventory/bin-scan` `qr_scan.py:106-158` — endpoint exists, no UI |
| Mark shipping unit delivered (POD) | Yes | Yes (camera + sig) | Yes | **Yes** | Reference implementation for R8.3 |
| Mark cabinet installed at customer site | No | No | No | n/a | R8.3 gap |
| Floor traveler record scan | Yes | Yes (calls button_finish) | Yes (if last WO) | No | `southbrook_floor_traveler` |
| Print cabinet label | Tap button | n/a | n/a | No | Could be scan-to-print from the cart |
| Trolley/cart binding to WO | No | n/a | n/a | n/a | R8.10 gap |

### B-3 · Physical-context tolerance by scenario

| Scenario | Today | Gap |
|---|---|---|
| 1. Cold start, tablet was off | Browser opens to last URL; Odoo session may be stale → login screen | R8.14 PIN gate halves login friction |
| 2. Glove typing | Default Odoo density; misses common | R8.1 density bundle |
| 3. Scan-first | POD + defect + floor traveler are scan-first; WO state transitions are NOT | R8.2 + R8.3 |
| 4. Single-hand | All status bars are top-of-form; tablet held one-handed → unreachable | R8.6 |
| 5. Dust / saw chips | Default 32-40 px buttons miss often | R8.1 |
| 6. Noise | Visual-only success/fail | R8.7 audio beeps |
| 7. Cold dark winter morning | Wall is dark; everything else is light | R8.5 dark mode |
| 8. Spilled coffee / browser-back | Unsaved field edits lost silently | R8.12 onbeforeunload |
| 9. Wi-Fi flake | All scans hard-fail | R8.4 IndexedDB queue |
| 10. Spanish-speaking operator | No es.po in any custom addon | R8.9 |
| 11. Trolley scan | No cart QR | R8.10 |
| 12. Power glove / fat-finger | Default density | R8.1 |
| 13. Paper traveler vs digital | Both exist; PDF is good; digital is dense | R8.13 operator kanban |

### B-4 · Native Odoo MRP mobile vs Southbrook custom

Native Odoo 19 ships `mrp_workorder` with a tablet/shop-floor view
(`/odoo/action-mrp_workorder.mrp_workorder_action_tablet` and the
`shop_floor` controller). **None of the Southbrook addons reference,
extend, or hide it.** It's there, unused. The Southbrook stack
re-implements floor flow via QR + the MO form rather than via Odoo's
own kiosk views.

Recommendation: **don't fight it**. Inherit `mrp_workorder.workorder_tablet_view`
(if present in CE) and layer the QR scan-to-finish on top via the
existing handler. If CE strips the tablet view (it sometimes does in
the Community edition), R8.13 is the build instead. Confirm before
choosing a path — `docker exec southbrook-odoo find ... -name '*tablet*'`
was unreachable from this review session.

### B-5 · Flutter `/app/` vs Odoo backend — when to use which

| Action | Flutter `/app/` | Odoo backend tablet |
|---|---|---|
| Customer viewing a project | **Flutter** — designed for it; secure-storage auth | Avoid |
| Customer approving a concept | **Flutter** — `approval_screen.dart` | Avoid |
| Customer uploading kitchen photo | **Flutter** — `photo_capture_screen.dart` is purpose-built | Avoid |
| Operator finishing a WO | Avoid — Flutter doesn't have it | **Odoo backend** (R8.2 closes the gap) |
| Filing an NCR | Avoid | **Odoo backend** (defect sheet wallpaper + scan) |
| Loading a truck | Avoid | **Odoo backend** (R8.8 binscan widget needed) |
| Marking POD at delivery | Avoid — too heavy | **Public POD page** (already implemented) |
| Marking install complete at customer site | Gap on both | **R8.3 public floor action mini-framework** |

The Flutter app is **customer-side**. The Odoo backend (plus public
HTML mini-pages) is **operator + driver + installer-side**. Don't
muddle. The biggest mistake to avoid is rebuilding floor flows in
Flutter — `[[southbrook_app_safari_blank_white]]` documents the
Cloudflare edge minification trap that ate the Flutter bundle and
remains unfixed. Floor screens cannot live on infrastructure with
this risk class.

---

## Notes on cross-references

- The wall kiosk + cabinet label + WO traveler + Flutter app are all
  named in the prompt as "existing assets". They are all real and
  well-built. This review **extends** them, never replaces.
- The OWL pre-commit hook (`[[southbrook_owl_precommit_hook]]`) and
  view-validation traps (`[[odoo19_view_validation_v19_traps]]`,
  `[[odoo19_view_dep_chain_manual_fields]]`) mean any new SCSS bundle
  or view inherit must land via a real `-i` cold install on a fresh
  DB, not just `-u`. Density bundle and dark theme should both be
  CI-gated through the drift detector
  (`[[southbrook_prod_module_drift_detector]]`).
- The QNAP `[[qnap_postgres_recovery_hazard]]` underlines R8.4 (offline
  queue) urgency: a 95-minute outage with zero operator buffer means
  90 minutes of lost-finish actions that operators may or may not
  re-do correctly when service resumes.

End of R8 review.
