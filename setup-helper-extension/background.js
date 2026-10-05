// Matches the account ID out of any of the BFF endpoints that carry it
// in the URL path (account-info, account-billing-details, upcoming-bill,
// transactions) - not just account-info - so whichever tab the user
// lands on first is enough to catch it.
const ACCOUNT_ID_URL_RE =
  /(?:energy-widgets-bff-api|billing-payments-bff)\/([a-f0-9-]{36})\//i;

// The account ID never appears in a cookie - only in the path of these
// BFF requests - so it has to be sniffed passively off real traffic
// rather than read directly, the way the cookie can be.
chrome.webRequest.onBeforeRequest.addListener(
  (details) => {
    // DEBUG: logs every request this extension sees to api.rx - open
    // chrome://extensions -> this extension -> "service worker" to view.
    // Safe to remove once account ID capture is confirmed working.
    console.log("[origin-setup-helper] saw request:", details.url);

    const match = details.url.match(ACCOUNT_ID_URL_RE);
    if (!match) return;
    console.log("[origin-setup-helper] matched account ID (webRequest):", match[1]);
    chrome.storage.local.set({ origin_account_id: match[1] });
  },
  { urls: ["https://api.rx.originenergy.com.au/*"] }
);

// Primary path: relayed from page-hook.js, which reads the real
// fetch/XHR response body in the page's own JS context - this is what
// actually works when the portal's Service Worker intercepts requests
// before they reach the network stack (where webRequest listens).
chrome.runtime.onMessage.addListener((message) => {
  if (message?.type === "account_id_found" && message.origin_account_id) {
    console.log(
      "[origin-setup-helper] matched account ID (page hook):",
      message.origin_account_id
    );
    chrome.storage.local.set({ origin_account_id: message.origin_account_id });
  }
});
