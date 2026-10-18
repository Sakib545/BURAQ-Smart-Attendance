/* BURAQ Smart Attendance — shell behaviour. No dependencies. */
(function () {
  "use strict";

  var root = document.documentElement;

  function store(key, value) {
    try { localStorage.setItem(key, value); } catch (e) { /* private mode */ }
  }

  function read(key) {
    try { return localStorage.getItem(key); } catch (e) { return null; }
  }

  function $(id) { return document.getElementById(id); }

  /* --- theme ------------------------------------------------------------
     The inline boot script in base.html has already resolved the theme from
     storage, falling back to the OS setting. This keeps the control's
     accessible state in step with it, which it never did before: the label
     read "Switch theme" forever and announced no pressed state.
  --------------------------------------------------------------------- */
  function paintThemeControl() {
    var dark = root.dataset.theme === "dark";
    var toggle = $("themeToggle");
    if (toggle) {
      toggle.setAttribute("aria-pressed", dark ? "true" : "false");
      toggle.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
    }
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", dark ? "#0e1512" : "#0b3b2e");
  }

  document.addEventListener("click", function (event) {
    if (!event.target.closest("#themeToggle")) return;
    root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
    store("buraq-theme", root.dataset.theme);
    paintThemeControl();
  });

  /* Follow the OS while the viewer has not made a choice of their own. */
  if (window.matchMedia) {
    var query = matchMedia("(prefers-color-scheme: dark)");
    var follow = function (event) {
      if (read("buraq-theme")) return;
      root.dataset.theme = event.matches ? "dark" : "light";
      paintThemeControl();
    };
    if (query.addEventListener) query.addEventListener("change", follow);
    else if (query.addListener) query.addListener(follow);
  }

  /* --- sidebar rail (desktop) ------------------------------------------- */
  function paintNavControl() {
    var toggle = $("navToggle");
    if (!toggle) return;
    var rail = root.dataset.nav === "rail";
    toggle.setAttribute("aria-pressed", rail ? "true" : "false");
    toggle.setAttribute("aria-label", rail ? "Expand navigation" : "Collapse navigation");
  }

  document.addEventListener("click", function (event) {
    if (!event.target.closest("#navToggle")) return;
    root.dataset.nav = root.dataset.nav === "rail" ? "full" : "rail";
    store("buraq-nav", root.dataset.nav);
    paintNavControl();
  });

  /* --- drawer (mobile) --------------------------------------------------
     Opening the drawer used to leave the page behind it scrollable, announce
     nothing to assistive tech, and drop keyboard focus wherever it was.
  --------------------------------------------------------------------- */
  var lastFocus = null;

  function paintDrawerControls(open) {
    ["menuToggle", "menuToggle2"].forEach(function (id) {
      var el = $(id);
      if (el) el.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  function openDrawer(trigger) {
    lastFocus = trigger || document.activeElement;
    root.setAttribute("data-drawer", "open");
    document.body.style.overflow = "hidden";
    paintDrawerControls(true);
    var first = document.querySelector("#sidebar a, #sidebar button");
    if (first) first.focus();
  }

  function closeDrawer() {
    if (root.getAttribute("data-drawer") !== "open") return;
    root.removeAttribute("data-drawer");
    document.body.style.overflow = "";
    paintDrawerControls(false);
    if (lastFocus && lastFocus.isConnected) lastFocus.focus();
    lastFocus = null;
  }

  document.addEventListener("click", function (event) {
    var trigger = event.target.closest("#menuToggle, #menuToggle2");
    if (trigger) {
      event.preventDefault();
      if (root.getAttribute("data-drawer") === "open") closeDrawer();
      else openDrawer(trigger);
      return;
    }
    if (event.target.closest(".scrim")) closeDrawer();
    /* Following a link inside the drawer should not leave it open behind
       the next page while the browser is still fetching it. */
    else if (event.target.closest("#sidebar a")) closeDrawer();
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closeDrawer();
  });

  /* Keep tabbing inside the drawer while it covers the page. */
  document.addEventListener("keydown", function (event) {
    if (event.key !== "Tab" || root.getAttribute("data-drawer") !== "open") return;
    var stops = $("sidebar").querySelectorAll("a[href], button:not([disabled])");
    if (!stops.length) return;
    var first = stops[0], last = stops[stops.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      last.focus();
      event.preventDefault();
    } else if (!event.shiftKey && document.activeElement === last) {
      first.focus();
      event.preventDefault();
    }
  });

  /* A drawer left open from a narrow viewport should not linger once the
     layout is wide enough to show the sidebar permanently. */
  if (window.matchMedia) {
    var wide = matchMedia("(min-width: 981px)");
    var settle = function (event) { if (event.matches) closeDrawer(); };
    if (wide.addEventListener) wide.addEventListener("change", settle);
    else if (wide.addListener) wide.addListener(settle);
  }

  paintThemeControl();
  paintNavControl();

  /* --- attendance ribbon ------------------------------------------------
     Renders <span class="ribbon" data-days="ppLaP..."> as day marks.
     p = present, l = late, a = absent, v = leave, . = no record.
  --------------------------------------------------------------------- */
  var states = { p: "present", l: "late", a: "absent", v: "leave" };
  var labels = { p: "present", l: "late", a: "absent", v: "on leave" };

  document.querySelectorAll(".ribbon[data-days]").forEach(function (node) {
    var days = node.getAttribute("data-days") || "";
    var html = "";
    var tally = { present: 0, late: 0, absent: 0, leave: 0 };
    for (var i = 0; i < days.length; i++) {
      var key = days[i];
      var state = states[key];
      if (state) tally[state]++;
      html += state
        ? '<i data-s="' + state + '" title="Day ' + (i + 1) + ': ' + labels[key] + '"></i>'
        : '<i title="Day ' + (i + 1) + ': no record"></i>';
    }
    node.innerHTML = html;
    /* The strip was pure colour, so it carried nothing for a screen reader. */
    if (!node.getAttribute("aria-label")) {
      node.setAttribute("role", "img");
      node.setAttribute("aria-label",
        "Last " + days.length + " days: " + tally.present + " present, " +
        tally.late + " late, " + tally.absent + " absent, " + tally.leave + " on leave");
    }
  });

  /* --- client-side list filter ------------------------------------------
     Any input with data-filter="<selector>" hides non-matching rows.
  --------------------------------------------------------------------- */
  document.querySelectorAll("input[data-filter]").forEach(function (input) {
    input.addEventListener("input", function () {
      var needle = input.value.trim().toLowerCase();
      var rows = document.querySelectorAll(input.getAttribute("data-filter"));
      var shown = 0;
      rows.forEach(function (row) {
        var hay = (row.getAttribute("data-search") || row.textContent || "").toLowerCase();
        var hit = !needle || hay.indexOf(needle) !== -1;
        row.style.display = hit ? "" : "none";
        if (hit) shown++;
      });
      /* Filtering everything out used to leave a blank panel with no
         explanation. Report the count instead. */
      var status = input.getAttribute("data-filter-status");
      var box = status ? document.querySelector(status) : null;
      if (box) {
        box.textContent = !needle
          ? ""
          : shown
            ? shown + " of " + rows.length + " shown"
            : "Nothing matches “" + input.value.trim() + "”";
      }
    });
  });
})();
