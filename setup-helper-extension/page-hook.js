// Injected into the MAIN world (the page's own JS context, not the
// extension's isolated one) so it sees fetch/XHR exactly as the page
// does - including responses served by the portal's own Service Worker,
// which never reach the network stack and so are invisible to
// chrome.webRequest entirely.
(() => {
  const ACCOUNT_INFO_RE = /energy-widgets-bff-api\/[a-f0-9-]{36}\/account-info/i;

  function report(accountId) {
    window.postMessage(
      { source: "origin-setup-helper", originAccountId: accountId },
      "*"
    );
  }

  function tryExtract(url, bodyText) {
    if (!url || !ACCOUNT_INFO_RE.test(url)) return;
    try {
      const data = JSON.parse(bodyText);
      if (data && data.originAccountId) report(data.originAccountId);
    } catch (e) {
      // not JSON, or shape changed - nothing to do
    }
  }

  const originalFetch = window.fetch;
  window.fetch = async function (...args) {
    const response = await originalFetch.apply(this, args);
    try {
      const url = typeof args[0] === "string" ? args[0] : args[0]?.url;
      if (url && ACCOUNT_INFO_RE.test(url)) {
        response
          .clone()
          .text()
          .then((text) => tryExtract(url, text))
          .catch(() => {});
      }
    } catch (e) {
      // never let instrumentation break the page's real request
    }
    return response;
  };

  const OriginalXHR = window.XMLHttpRequest;
  const originalOpen = OriginalXHR.prototype.open;
  OriginalXHR.prototype.open = function (method, url, ...rest) {
    this.__originSetupHelperUrl = url;
    return originalOpen.call(this, method, url, ...rest);
  };
  const originalSend = OriginalXHR.prototype.send;
  OriginalXHR.prototype.send = function (...args) {
    this.addEventListener("load", () => {
      tryExtract(this.__originSetupHelperUrl, this.responseText);
    });
    return originalSend.apply(this, args);
  };
})();
