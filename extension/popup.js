const $ = (id) => document.getElementById(id);

async function render() {
  const s = await chrome.storage.local.get(["token", "employee", "tracking", "lastSync", "error", "server"]);
  $("error").textContent = s.error || "";
  $("paired").hidden = !s.token;
  $("setup").hidden = !!s.token;
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
    { type: "pair", code: $("code").value, label: $("label").value, server: $("server").value },
    (result) => {
      if (result && result.ok) render();
      else $("error").textContent = (result && result.message) || "Could not connect.";
    }
  );
});

chrome.storage.onChanged.addListener(render);
chrome.runtime.sendMessage({ type: "sync" }, () => { void chrome.runtime.lastError; render(); });
render();
