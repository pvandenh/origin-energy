function setField(inputId, statusId, value, foundLabel, missingLabel) {
  const input = document.getElementById(inputId);
  const status = document.getElementById(statusId);
  if (value) {
    input.value = value;
    status.textContent = foundLabel;
    status.className = "status ok";
  } else {
    input.value = "";
    input.placeholder = "not found yet";
    status.textContent = missingLabel;
    status.className = "status missing";
  }
}

async function load() {
  // Cookie is read live every time the popup opens - no need to wait
  // for a background listener to catch it, since reading it directly
  // is always available and always current.
  const cookie = await chrome.cookies.get({
    url: "https://id.originenergy.com.au",
    name: "auth0",
  });
  setField(
    "cookie",
    "cookie-status",
    cookie ? cookie.value : null,
    "Found",
    "Not found - make sure you're logged in"
  );

  const stored = await chrome.storage.local.get("origin_account_id");
  setField(
    "account-id",
    "account-id-status",
    stored.origin_account_id || null,
    "Found",
    "Not seen yet - visit the account/billing page once"
  );
}

document.querySelectorAll("button[data-copy]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const input = document.getElementById(btn.dataset.copy);
    if (!input.value) return;
    await navigator.clipboard.writeText(input.value);
    const original = btn.textContent;
    btn.textContent = "Copied!";
    setTimeout(() => (btn.textContent = original), 1200);
  });
});

load();
