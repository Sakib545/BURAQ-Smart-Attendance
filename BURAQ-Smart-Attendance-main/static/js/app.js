/* BURAQ Smart Attendance — shell behaviour. No dependencies. */
(function () {
  "use strict";

  var root = document.documentElement;

  function store(key, value) {
    try { localStorage.setItem(key, value); } catch (e) { /* private mode */ }
  }

  /* --- theme ------------------------------------------------------------ */
  document.addEventListener("click", function (event) {
    var toggle = event.target.closest("#themeToggle");
    if (!toggle) return;
    var next = root.dataset.theme === "dark" ? "light" : "dark";
    root.dataset.theme = next;
    store("buraq-theme", next);
    toggle.setAttribute("aria-label", next === "dark" ? "Switch to light theme" : "Switch to dark theme");
  });

  /* --- sidebar rail (desktop) ------------------------------------------- */
  document.addEventListener("click", function (event) {
    if (!event.target.closest("#navToggle")) return;
    var next = root.dataset.nav === "rail" ? "full" : "rail";
    root.dataset.nav = next;
    store("buraq-nav", next);
  });

  /* --- drawer (mobile) -------------------------------------------------- */
  function closeDrawer() { root.removeAttribute("data-drawer"); }

  document.addEventListener("click", function (event) {
    if (event.target.closest("#menuToggle")) {
      if (root.getAttribute("data-drawer") === "open") closeDrawer();
      else root.setAttribute("data-drawer", "open");
      return;
    }
    if (event.target.closest(".scrim")) closeDrawer();
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closeDrawer();
  });

  /* --- attendance ribbon ------------------------------------------------
     Renders <span class="ribbon" data-days="ppLaP..."> as day marks.
     p = present, l = late, a = absent, v = leave, . = no record.
  --------------------------------------------------------------------- */
  var states = { p: "present", l: "late", a: "absent", v: "leave" };

  document.querySelectorAll(".ribbon[data-days]").forEach(function (node) {
    var days = node.getAttribute("data-days") || "";
    var html = "";
    for (var i = 0; i < days.length; i++) {
      var state = states[days[i]];
      html += state ? '<i data-s="' + state + '"></i>' : "<i></i>";
    }
    node.innerHTML = html;
  });

  /* --- client-side list filter ------------------------------------------
     Any input with data-filter="<selector>" hides non-matching rows.
  --------------------------------------------------------------------- */
  document.querySelectorAll("input[data-filter]").forEach(function (input) {
    input.addEventListener("input", function () {
      var needle = input.value.trim().toLowerCase();
      document.querySelectorAll(input.getAttribute("data-filter")).forEach(function (row) {
        var hay = (row.getAttribute("data-search") || row.textContent || "").toLowerCase();
        row.style.display = !needle || hay.indexOf(needle) !== -1 ? "" : "none";
      });
    });
  });
})();
