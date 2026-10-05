// page-hook.js runs in the page's own JS world and has no access to
// chrome.* APIs. This script runs in the extension's normal isolated
// world (which does have that access) and just relays the message
// across - window.postMessage is the one channel both worlds share.
window.addEventListener("message", (event) => {
  if (event.source !== window) return;
  const data = event.data;
  if (!data || data.source !== "origin-setup-helper" || !data.originAccountId) {
    return;
  }
  chrome.runtime.sendMessage({
    type: "account_id_found",
    origin_account_id: data.originAccountId,
  });
});
