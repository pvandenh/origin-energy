<img width="738" height="414" alt="image" src="https://github.com/user-attachments/assets/0e31f2cd-354f-4b02-8e4b-8644a6c4c0ea" />


# Origin Energy (Unofficial) for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)


A custom Home Assistant integration for [Origin Energy](https://www.originenergy.com.au/)
(Australia) account, billing, and EV Power Up (EV iCharge) data.

> **This is not an official Origin Energy product.** Every endpoint here
> was reverse-engineered from a captured browser session against
> Origin's consumer portal. It is undocumented, unsupported, and can
> break without notice. Use at your own risk.

## Features

- Account balance, bill period, cost-to-date, cost-per-day, predicted bill
- Today's consumption / feed-in / cost, in kWh and AUD
- Payment schedule, due bills, transaction count
- Smart meter and account-state binary sensors
- **EV Power Up (EV iCharge)**, per vehicle:
  - Battery level, range, charge status (Unplugged / Charging / Paused / Complete)
  - Target charge time (writable)
  - Instant Charge switch (automatically unavailable when there's nothing
    useful it can do - unplugged, already complete, or status unknown)
  - Solar optimisation / target-at-risk binary sensors

## Installation

### Via HACS (custom repository)

This isn't in the default HACS store, so add it as a custom repository:

1. HACS → Integrations → ⋮ (top right) → **Custom repositories**
2. Repository: `https://github.com/pvandenh/origin-energy`, Category: **Integration**
3. Find "Origin Energy (Unofficial)" in HACS and install
4. Restart Home Assistant

### Manual

Copy `custom_components/origin_energy/` into your Home Assistant
`config/custom_components/` folder and restart.

## Setup

The portal has no API Origin intends for third-party use, and the
interactive login form is behind bot detection that this integration
deliberately never tries to automate (see [`auth.py`](custom_components/origin_energy/auth.py)
for why). Instead, setup needs two values pulled from your own already
logged-in browser session:

- **`auth0` session cookie** - lets the integration silently mint fresh
  access tokens indefinitely, without touching the guarded login form
- **Origin account ID** - a UUID, not your `A-XXXXXXX` account number

### Easiest: the setup helper extension

[`setup-helper-extension/`](setup-helper-extension/) is a small, unpublished
browser extension that finds both values for you and puts them in a
copy-paste-ready popup - no DevTools required. See its own
[README](setup-helper-extension/README.md) for load-unpacked instructions.
<img width="1716" height="483" alt="image" src="https://github.com/user-attachments/assets/b8b54e76-3c4b-4efb-9728-8af83b68cb19" />




### Manual fallback

1. Open DevTools (F12) → **Network** tab → tick **Preserve log**
2. Log in to originenergy.com.au normally
3. Filter for `account-info` - one request appears, shaped like
   `energy-widgets-bff-api/<uuid>/account-info`
4. Click it → **Preview** tab → copy the `originAccountId` field directly
5. Same request → **Headers** → **Request Headers** → `Cookie:` line →
   copy everything between `auth0=` and the next semicolon

Paste both into the integration's config flow.

### If your session expires

Origin's own session (not just the short-lived access token) can
eventually lapse. If it does, Home Assistant will raise a Reauthenticate
repair - repeat the cookie-capture step above and paste the fresh value
into the reauth form.

## Known limitations

- **Read-only except target charge time and Instant Charge.** Everything
  else (billing, usage, account state) is read-only by design - there's
  no write endpoint captured for it.
- **Half-hourly usage data was never observed**, only daily granularity.
- **Cookie lifetime is unverified.** Origin stated ~3 months as an upper
  bound at capture time; actual expiry (including any inactivity
  timeout) hasn't been confirmed.
- **`charge_status` is normalized from raw API values** by substring
  match (`unplugged`, `charging`, `paused`, `complete`), since Origin's
  full set of possible raw values isn't documented. An unrecognised raw
  value reports as `unknown` rather than being guessed into one of the
  four.
- Not tested against a non-EV-Power-Up account, a second Origin account,
  or a different plan type.

## Contributing

Issues and PRs welcome - particularly captured HAR traffic for any
endpoint not yet wired up (gas accounts, half-hourly usage, additional
EV writes like toggling solar optimisation). See the per-file docstrings
for what's already been reverse-engineered and what's still a guess.

## Disclaimer

Not affiliated with, endorsed by, or supported by Origin Energy Limited.
"Origin Energy" and associated logos are trademarks of their respective
owner, used here only to describe the service this integration talks to.

## License

[MIT](LICENSE)
