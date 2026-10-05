// BURAQ Duty Browsing Tracker — background service worker.
// Stores only {domain: seconds}. Never URLs, titles or page content.
importScripts("config.js");
const CONFIG = self.BURAQ_CONFIG || {};
const DEFAULT_SERVER = CONFIG.server || "https://smart-attendance.pro";
const MAX_TICK_SECONDS = 120;   // longer gaps (sleep, worker stopped) are not counted
const IDLE_SECONDS = 120;

const get = (keys) => chrome.storage.local.get(keys);
const set = (values) => chrome.storage.local.set(values);

function domainOf(url) {
  try {
    const u = new URL(url);
    if (u.protocol !== "http:" && u.protocol !== "https:") return "";
    return u.hostname.toLowerCase().replace(/^www\./, "");
  } catch (e) {
    return "";
  }
}

async function activeDomain() {
  const state = await chrome.idle.queryState(IDLE_SECONDS);
  if (state !== "active") return "";
  const win = await chrome.windows.getLastFocused({ populate: false }).catch(() => null);
  if (!win || !win.focused) return "";
  const [tab] = await chrome.tabs.query({ active: true, windowId: win.id });
  if (!tab || tab.incognito) return "";
  return domainOf(tab.url || "");
}

// Credit the time since the last tick to whichever site was active then.
let ticking = Promise.resolve();
function tick() {
  ticking = ticking.then(doTick, doTick);
  return ticking;
}
async function doTick() {
  const now = Date.now();
  const s = await get(["token", "tracking", "current", "lastTick", "pending"]);
  const pending = s.pending || {};
  if (s.token && s.tracking && s.current && s.lastTick) {
    const elapsed = Math.round((now - s.lastTick) / 1000);
    if (elapsed > 0 && elapsed <= MAX_TICK_SECONDS) {
      pending[s.current] = (pending[s.current] || 0) + elapsed;
    }
  }
  const current = s.token && s.tracking ? await activeDomain() : "";
  await set({ pending, current, lastTick: now });
}

async function api(path, options = {}) {
  const s = await get(["server", "token"]);
  const headers = { "Content-Type": "application/json" };
  if (s.token) headers.Authorization = "Bearer " + s.token;
  const response = await fetch((s.server || DEFAULT_SERVER) + path, { ...options, headers });
  const data = await response.json().catch(() => ({}));
  return { status: response.status, data };
}

// Downloaded from a personal install link: connect without asking for a code.
// A personal install link open in any tab also identifies the employee, so a
// copy installed from the Chrome Web Store connects itself the same way.
async function inviteFromTabs() {
  try {
    // Filtered here rather than with a URL pattern: patterns cannot carry a port.
    const prefix = DEFAULT_SERVER + "/tracker/i/";
    for (const tab of await chrome.tabs.query({})) {
      const url = tab.url || "";
      if (!url.startsWith(prefix)) continue;
      const token = url.slice(prefix.length).split(/[\/?#]/)[0];
      if (token) return token;
    }
  } catch (e) { /* no matching tab */ }
  return "";
}

async function autoPair() {
  const s = await get(["token", "inviteRejected"]);
  if (s.token) return;
  const invite = CONFIG.invite || (await inviteFromTabs());
  if (!invite || s.inviteRejected === invite) return;
  try {
    await set({ server: DEFAULT_SERVER });
    const result = await api("/api/browsing/pair", { method: "POST", body: JSON.stringify({ invite }) });
    if (result.status === 200 && result.data.ok) {
      await set({ token: result.data.token, employee: result.data.employee, tracking: !!result.data.tracking,
                  pending: {}, current: "", lastTick: Date.now(), error: "" });
    } else if (result.status === 400) {
      await set({ inviteRejected: invite, error: result.data.message || "Install link is no longer valid." });
    }
  } catch (e) {
    await set({ error: "Cannot reach the server. Will retry." });
  }
}

async function sync() {
  await autoPair();
  await tick();
  const s = await get(["token", "pending", "tracking"]);
  if (!s.token) return;
  const entries = Object.entries(s.pending || {}).map(([domain, seconds]) => ({ domain, seconds }));
  try {
    const result = entries.length
      ? await api("/api/browsing/report", { method: "POST", body: JSON.stringify({ entries }) })
      : await api("/api/browsing/status");
    if (result.status === 401) {
      await set({ token: "", tracking: false, pending: {}, current: "", employee: "", error: "This PC was disconnected by Admin." });
      return;
    }
    if (result.status === 200 && result.data.ok) {
      const update = { tracking: !!result.data.tracking, lastSync: Date.now(), error: "" };
      if (result.data.employee) update.employee = result.data.employee;
      if (entries.length) {
        // Subtract what was sent; keep anything added while the request ran.
        const latest = (await get(["pending"])).pending || {};
        for (const e of entries) {
          const left = (latest[e.domain] || 0) - e.seconds;
          if (left > 0) latest[e.domain] = left; else delete latest[e.domain];
        }
        update.pending = update.tracking ? latest : {};
      }
      if (!update.tracking) { update.pending = {}; update.current = ""; }
      await set(update);
    }
  } catch (e) {
    await set({ error: "Cannot reach the server. Will retry." });
  }
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create("buraq-sync", { periodInMinutes: 1 });
  chrome.idle.setDetectionInterval(IDLE_SECONDS);
  sync();
});
chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create("buraq-sync", { periodInMinutes: 1 });
  chrome.idle.setDetectionInterval(IDLE_SECONDS);
});
chrome.alarms.onAlarm.addListener((alarm) => { if (alarm.name === "buraq-sync") sync(); });
chrome.tabs.onActivated.addListener(() => tick());
chrome.tabs.onUpdated.addListener((id, change, tab) => {
  if (change.url) tick();
  if (change.status === "complete" && (tab.url || "").startsWith(DEFAULT_SERVER + "/tracker/i/")) sync();
});
chrome.windows.onFocusChanged.addListener(() => tick());
chrome.idle.onStateChanged.addListener(() => tick());

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (message.type === "pair") {
    (async () => {
      try {
        const server = (message.server || DEFAULT_SERVER).trim().replace(/\/+$/, "");
        await set({ server, token: "" });
        const result = await api("/api/browsing/pair", {
          method: "POST",
          body: JSON.stringify({ staff_id: message.staff_id, code: message.code, label: message.label }),
        });
        if (result.status === 200 && result.data.ok) {
          await set({ token: result.data.token, employee: result.data.employee, tracking: !!result.data.tracking,
                      pending: {}, current: "", lastTick: Date.now(), error: "" });
          chrome.alarms.create("buraq-sync", { periodInMinutes: 1 });
          respond({ ok: true });
        } else {
          respond({ ok: false, message: result.data.message || "Could not connect." });
        }
      } catch (e) {
        respond({ ok: false, message: "Cannot reach the server. Check the address." });
      }
    })();
    return true;
  }
  if (message.type === "sync") { sync().then(() => respond({ ok: true })); return true; }
});
