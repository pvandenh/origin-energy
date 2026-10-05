# Origin Energy HA Setup Helper

A small browser extension that finds the two values the
[Origin Energy integration](../README.md) needs during setup - the
`auth0` session cookie and your Origin account ID - and shows them in a
popup with copy buttons. No DevTools required.

Not published to the Chrome Web Store (it's a single-purpose helper for
this integration, not a general-audience extension), so it's loaded
unpacked.

## Install

1. Download or clone this repo
2. Go to `chrome://extensions` (or the equivalent in any Chromium
   browser - Edge, Brave)
3. Enable **Developer mode** (top right)
4. Click **Load unpacked** and select this `setup-helper-extension/`
   folder
5. Pin it to the toolbar if you like

## Use

1. Log in to originenergy.com.au normally
2. Click into the account/billing page once, so a real request fires
3. Click the extension's toolbar icon
4. Copy both values into the Home Assistant config flow

If a field says "not found yet": make sure you're logged in and have
actually loaded the account/billing page since installing the
extension - a full page reload (not just clicking between
already-loaded tabs) is sometimes needed to trigger a fresh request.

## How it works

- **Cookie**: read directly via the `cookies` permission - no capture
  needed, it's just always current.
- **Account ID**: Origin's portal appears to serve some of its own API
  responses through a Service Worker, which can bypass Chrome's network
  stack entirely (and with it, the `webRequest` API most extensions
  would use to see this). `page-hook.js` instead patches `fetch` and
  `XMLHttpRequest` from inside the page's own JavaScript context, reads
  the real `account-info` response body, and relays the
  `originAccountId` field back to the extension.

## Permissions

- `cookies` - to read the `auth0` cookie
- `webRequest` / `storage` - best-effort fallback account-ID capture,
  and storing whatever's been found
- Host access to `id.originenergy.com.au`, `api.rx.originenergy.com.au`,
  and `www.originenergy.com.au` - nothing else
