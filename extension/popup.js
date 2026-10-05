const $ = (id) => document.getElementById(id);

async function render() {
  const s = await chrome.storage.local.get(["token", "employee", "tracking", "lastSync", "error", "server"]);
  if (!s.server && (self.BURAQ_CONFIG || {}).server) s.server = self.BURAQ_CONFIG.server;
  $("error").textContent = s.error || "";
  $("paired").hidden = !s.token;
  // With a personal install link the extension connects itself; the code form
  // only appears if that link was cancelled.
  const waiting = !s.token && !!(self.BURAQ_CONFIG || {}).invite && !s.error;
  $("auto").hidden = !waiting;
  $("setup").hidden = !!s.token || waiting;
  if (s.server) $("server").value = s.server;
  if (!s.token) return;
  $("employee").textContent = s.employee || "";
  $("state").textContent = s.tracking ? "On duty — recording" : "Off duty — not recording";
  $("state").className = "pill " + (s.tracking ? "on" : "off");
  $("detail").textContent = s.lastSync ? "Last sync: " + new Date(s.lastSync).toLocaleTimeString() : "Waiting for first sync…";
}

$("setup").addEventListener("submit", (event) => {
  event.preventDefault();
  $("error").textContent = "Connecting…";
  chrome.runtime.sendMessage(
    { type: "pair", staff_id: $("staff").value.trim(), server: $("server").value },
    (result) => {
      if (result && result.ok) render();
      else $("error").textContent = (result && result.message) || "Could not connect.";
    }
  );
});

chrome.storage.onChanged.addListener(render);
chrome.runtime.sendMessage({ type: "sync" }, () => { void chrome.runtime.lastError; render(); });
render();
