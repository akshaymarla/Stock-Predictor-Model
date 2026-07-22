(function () {
  "use strict";

  var data = window.SCREENER_DATA;

  if (!data) {
    document.querySelector("main").innerHTML =
      '<div class="data-error">No screener data found.<br><br>' +
      "Run <code>python src/export_screener_data.py</code> from the repo root " +
      "to generate <code>frontend/data.js</code>, then reload this page.</div>";
    document.getElementById("ticker-header").hidden = true;
    document.getElementById("sidebar").hidden = true;
    return;
  }

  var state = { overviewHorizon: null, statHorizon: null, compare: [null, null] };

  // ==================================================================
  // Router
  // ==================================================================

  var SECTIONS_WITH_TICKER = { overview: true, statistical: true, compare: true };

  function goToSection(name) {
    document.querySelectorAll(".app-section").forEach(function (el) {
      el.hidden = el.id !== "section-" + name;
    });
    document.querySelectorAll(".nav-item").forEach(function (el) {
      if (el.getAttribute("data-section") === name) {
        el.setAttribute("aria-current", "page");
      } else {
        el.removeAttribute("aria-current");
      }
    });
    document.getElementById("ticker-header").hidden = !SECTIONS_WITH_TICKER[name];

    if (name === "compare") {
      ensureUniverseLoaded();
    }
  }

  document.querySelectorAll(".nav-item").forEach(function (item) {
    item.addEventListener("click", function () {
      goToSection(item.getAttribute("data-section"));
    });
  });
  document.querySelectorAll("[data-goto]").forEach(function (el) {
    el.addEventListener("click", function () {
      goToSection(el.getAttribute("data-goto"));
    });
  });

  // ==================================================================
  // Ticker header (persistent across Overview/Statistical/Compare)
  // ==================================================================

  function renderTicker() {
    var nifty = data.nifty || {};
    var valueEl = document.getElementById("ticker-value");
    var changeEl = document.getElementById("ticker-change");
    var dateEl = document.getElementById("ticker-date");

    if (nifty.close == null) {
      valueEl.textContent = "N/A";
      valueEl.classList.remove("skeleton");
      changeEl.textContent = "";
      dateEl.innerHTML = "Data unavailable";
      return;
    }

    valueEl.classList.remove("skeleton");
    valueEl.textContent = formatThousands(nifty.close, 2);

    if (nifty.day_change_pct != null) {
      var isPos = nifty.day_change_pct >= 0;
      changeEl.className = "ticker-change " + (isPos ? "pos" : "neg");
      changeEl.textContent = (isPos ? "▲ " : "▼ ") + Math.abs(nifty.day_change_pct).toFixed(2) + "%";
    } else {
      changeEl.textContent = "";
    }

    var dateLabel = formatDate(nifty.as_of_date);
    if (nifty.is_stale) {
      dateEl.innerHTML = "AS OF " + dateLabel + '<span class="close-label">LAST AVAILABLE CLOSE</span>';
    } else {
      dateEl.innerHTML = dateLabel + '<span class="close-label">CLOSE</span>';
    }
  }

  // ==================================================================
  // Overview (existing build, unchanged behavior)
  // ==================================================================

  function renderFootnote() {
    var el = document.getElementById("picker-footnote");
    el.classList.remove("skeleton");
    var meta = data.model_meta || {};
    el.classList.add("status-" + (meta.status || "provisional"));
    if (meta.status === "validated") {
      el.textContent = "MODEL RUN " + (meta.run_date || "") + " · " + (meta.notes || "VALIDATED");
    } else {
      el.textContent = (meta.notes || "MODEL OUTPUT PENDING RE-VALIDATION").toUpperCase();
    }
  }

  document.querySelectorAll("#overview-picker-tiles .picker-tile").forEach(function (tile) {
    tile.addEventListener("click", function () {
      showDeck(tile.getAttribute("data-horizon"));
    });
  });

  document.getElementById("back-button").addEventListener("click", function () {
    document.getElementById("screen-deck").hidden = true;
    document.getElementById("screen-picker").hidden = false;
  });

  var TOP_N = 20; // matches weekly_shortlist.py's / export_screener_data.py's own top-N convention

  function showDeck(horizon) {
    state.overviewHorizon = horizon;
    document.getElementById("screen-picker").hidden = true;
    document.getElementById("screen-deck").hidden = false;
    renderDeck();
  }

  function renderDeck() {
    var horizon = state.overviewHorizon;
    var probKey = "prob_" + horizon;
    var candidates = (data.candidates || []).slice();

    candidates.sort(function (a, b) {
      var diff = (b[probKey] || 0) - (a[probKey] || 0);
      if (diff !== 0) return diff;
      return a.ticker.localeCompare(b.ticker); // tiebreak: alphabetical
    });
    // data.candidates is already the union of both horizons' top-N
    // (export_screener_data.py) -- re-slicing to this horizon's own
    // top-N here is what actually keeps a stock that only made the
    // OTHER horizon's cut out of this tab. Sorting by this horizon's
    // prob and taking the top N reproduces the true full-universe top-N
    // exactly, since every stock that's really in this horizon's top N
    // is already present in the union by construction.
    candidates = candidates.slice(0, TOP_N);

    document.getElementById("deck-title").textContent = horizon.toUpperCase() + " outperformance candidates";
    document.getElementById("deck-count").textContent = "N=" + candidates.length + " CANDIDATES";

    var grid = document.getElementById("deck-grid");
    var emptyEl = document.getElementById("deck-empty");

    if (candidates.length === 0) {
      grid.innerHTML = "";
      emptyEl.hidden = false;
      return;
    }
    emptyEl.hidden = true;

    grid.innerHTML = candidates.map(function (c, i) {
      return renderCard(c, horizon, probKey, i + 1);
    }).join("");
  }

  function renderCard(c, horizon, probKey, rank) {
    var prob = c[probKey] || 0;
    var probPct = Math.round(prob * 100);
    var changeIsPos = (c.day_change_pct || 0) >= 0;
    var changeGlyph = changeIsPos ? "▲" : "▼";
    var changeClass = changeIsPos ? "pos" : "neg";

    return (
      '<div class="card">' +
        '<div class="card-header">' +
          '<div class="card-logo">' + escapeHtml(c.ticker.slice(0, 4).toUpperCase()) + "</div>" +
          "<div>" +
            '<div class="card-company">' + escapeHtml(c.company_name) + "</div>" +
            '<div class="card-subline">' + escapeHtml(c.sector) + " · " + escapeHtml(c.ticker) + "</div>" +
          "</div>" +
        "</div>" +
        '<div class="card-prob">' + probPct + "%</div>" +
        '<div class="card-price">₹' + formatThousands(c.eod_price, 2) + "</div>" +
        '<div class="card-range">52W RANGE ₹' + formatThousands(c.range_52w_low, 0) +
          " – ₹" + formatThousands(c.range_52w_high, 0) + "</div>" +
        '<div class="card-fill-track"><div class="card-fill-bar" style="width:' + probPct + '%"></div></div>' +
        '<div class="card-footer">' +
          "<span>" + horizon.toUpperCase() + "</span>" +
          '<span class="' + changeClass + '">' + changeGlyph + " " + Math.abs(c.day_change_pct || 0).toFixed(2) + "%</span>" +
          "<span>#" + rank + "</span>" +
        "</div>" +
      "</div>"
    );
  }

  // ==================================================================
  // Statistical View
  // ==================================================================

  document.querySelectorAll("#stat-picker-tiles .picker-tile").forEach(function (tile) {
    tile.addEventListener("click", function () {
      showStatTable(tile.getAttribute("data-horizon"));
    });
  });

  document.getElementById("stat-back-button").addEventListener("click", function () {
    document.getElementById("stat-table-screen").hidden = true;
    document.getElementById("stat-picker").hidden = false;
  });

  function showStatTable(horizon) {
    state.statHorizon = horizon;
    document.getElementById("stat-picker").hidden = true;
    document.getElementById("stat-table-screen").hidden = false;
    renderStatTable();
  }

  function renderStatTable() {
    var horizon = state.statHorizon;
    var tracking = (data.tracking || {})[horizon];
    var tableEl = document.getElementById("stat-table");
    var emptyEl = document.getElementById("stat-empty");

    document.getElementById("stat-title").textContent = horizon.toUpperCase() + " tracking -- realized vs. Nifty";

    if (!tracking) {
      tableEl.innerHTML = "";
      emptyEl.hidden = false;
      document.getElementById("stat-pick-date").textContent = "";
      return;
    }
    emptyEl.hidden = true;
    document.getElementById("stat-pick-date").textContent = "PICKED " + formatDate(tracking.pick_date);

    // default row order: same as Overview deck for this horizon (ranked
    // by probability, descending) -- cross-referencing data.candidates
    // since tracking.stocks itself only carries realized closes, not
    // probabilities. A symbol not found there (sets can diverge -- see
    // export_screener_data.py's tracking_section docstring) sorts after,
    // alphabetically, rather than being dropped.
    var probKey = "prob_" + horizon;
    var probByTicker = {};
    (data.candidates || []).forEach(function (c) { probByTicker[c.ticker] = c[probKey]; });

    var tickers = Object.keys(tracking.stocks).sort(function (a, b) {
      var pa = probByTicker[a], pb = probByTicker[b];
      if (pa != null && pb != null && pa !== pb) return pb - pa;
      if (pa != null && pb == null) return -1;
      if (pa == null && pb != null) return 1;
      return a.localeCompare(b);
    });

    var head = "<thead><tr><th class=\"row-header\">Symbol</th>" +
      tracking.trading_days.map(function (d, i) {
        return '<th>D+' + i + '<span class="col-date">' + formatDateShort(d) + '</span></th>';
      }).join("") + "</tr></thead>";

    var niftyRow = '<tr class="nifty-row"><th class="row-header">NIFTY 50</th>' +
      tracking.trading_days.map(function (d) { return statCell(tracking.nifty_history[d]); }).join("") + "</tr>";

    var stockRows = tickers.map(function (ticker) {
      var series = tracking.stocks[ticker];
      return '<tr><td class="row-header">' + escapeHtml(ticker) + "</td>" +
        tracking.trading_days.map(function (d) { return statCell(series[d]); }).join("") + "</tr>";
    }).join("");

    tableEl.innerHTML = head + "<tbody>" + niftyRow + stockRows + "</tbody>";
  }

  function statCell(entry) {
    if (!entry || entry.close == null) {
      return '<td><span class="stat-cell-empty">–</span></td>';
    }
    var changeHtml = "";
    if (entry.day_change_pct != null) {
      var isPos = entry.day_change_pct >= 0;
      changeHtml = '<span class="stat-cell-change ' + (isPos ? "pos" : "neg") + '">' +
        (isPos ? "+" : "") + entry.day_change_pct.toFixed(2) + "%</span>";
    }
    return '<td><span class="stat-cell-close">₹' + formatThousands(entry.close, 2) + "</span>" + changeHtml + "</td>";
  }

  // ==================================================================
  // Other Stocks -- To Compare
  // ==================================================================

  var universeLoadState = "idle"; // idle | loading | loaded | error

  function ensureUniverseLoaded() {
    if (universeLoadState !== "idle") return;
    universeLoadState = "loading";
    setSearchEnabled(false, "Loading full stock list…");
    var script = document.createElement("script");
    script.src = "universe.js";
    script.onload = function () {
      universeLoadState = "loaded";
      setSearchEnabled(true, "Search for a stock by name or ticker…");
    };
    script.onerror = function () {
      universeLoadState = "error";
      setSearchEnabled(false, "Could not load universe.js -- run the export script first.");
    };
    document.body.appendChild(script);
  }

  function setSearchEnabled(enabled, placeholder) {
    [1, 2].forEach(function (n) {
      var input = document.getElementById("compare-search-" + n);
      if (input) {
        input.disabled = !enabled;
        input.placeholder = placeholder;
      }
    });
  }

  function universeStocks() {
    return (window.UNIVERSE_SNAPSHOT || {}).stocks || {};
  }

  function matchStocks(query) {
    var q = query.trim().toLowerCase();
    if (!q) return [];
    var stocks = universeStocks();
    var matches = [];
    Object.keys(stocks).forEach(function (ticker) {
      var s = stocks[ticker];
      var name = (s.company_name || "").toLowerCase();
      if (ticker.toLowerCase().indexOf(q) !== -1 || name.indexOf(q) !== -1) {
        matches.push({ ticker: ticker, company_name: s.company_name });
      }
    });
    matches.sort(function (a, b) { return a.company_name.localeCompare(b.company_name); });
    return matches.slice(0, 25);
  }

  function setupCompareSearch(slotIndex) {
    var input = document.getElementById("compare-search-" + slotIndex);
    var dropdown = document.getElementById("compare-dropdown-" + slotIndex);
    var activeIndex = -1;
    var currentMatches = [];

    function closeDropdown() {
      dropdown.hidden = true;
      input.setAttribute("aria-expanded", "false");
      activeIndex = -1;
    }

    function renderDropdown(matches) {
      currentMatches = matches;
      activeIndex = -1;
      if (!matches.length) {
        closeDropdown();
        return;
      }
      dropdown.innerHTML = matches.map(function (m, i) {
        return '<li class="compare-option" role="option" data-index="' + i + '">' +
          '<span class="opt-name">' + escapeHtml(m.company_name) + "</span>" +
          '<span class="opt-ticker">' + escapeHtml(m.ticker) + "</span></li>";
      }).join("");
      dropdown.hidden = false;
      input.setAttribute("aria-expanded", "true");
    }

    function selectMatch(m) {
      input.value = m.company_name + " (" + m.ticker + ")";
      closeDropdown();
      selectCompareStock(slotIndex, m.ticker);
    }

    input.addEventListener("input", function () {
      renderDropdown(matchStocks(input.value));
    });

    input.addEventListener("keydown", function (e) {
      if (dropdown.hidden) return;
      var items = dropdown.querySelectorAll(".compare-option");
      if (e.key === "ArrowDown") {
        e.preventDefault();
        activeIndex = Math.min(activeIndex + 1, items.length - 1);
        updateActive(items);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        activeIndex = Math.max(activeIndex - 1, 0);
        updateActive(items);
      } else if (e.key === "Enter") {
        e.preventDefault();
        if (activeIndex >= 0 && currentMatches[activeIndex]) selectMatch(currentMatches[activeIndex]);
      } else if (e.key === "Escape") {
        closeDropdown();
      }
    });

    function updateActive(items) {
      items.forEach(function (el, i) { el.classList.toggle("active", i === activeIndex); });
      if (items[activeIndex]) items[activeIndex].scrollIntoView({ block: "nearest" });
    }

    dropdown.addEventListener("click", function (e) {
      var li = e.target.closest(".compare-option");
      if (!li) return;
      var idx = parseInt(li.getAttribute("data-index"), 10);
      if (currentMatches[idx]) selectMatch(currentMatches[idx]);
    });

    document.addEventListener("click", function (e) {
      if (!dropdown.contains(e.target) && e.target !== input) closeDropdown();
    });
  }

  setupCompareSearch(1);
  setupCompareSearch(2);

  document.getElementById("compare-add-button").addEventListener("click", function () {
    document.getElementById("compare-search-row-2").hidden = false;
    document.getElementById("compare-add-button").hidden = true;
    document.getElementById("compare-search-2").focus();
  });

  document.getElementById("compare-remove-button").addEventListener("click", function () {
    state.compare[1] = null;
    document.getElementById("compare-search-2").value = "";
    document.getElementById("compare-search-row-2").hidden = true;
    document.getElementById("compare-add-button").hidden = false;
    renderCompare();
  });

  function selectCompareStock(slotIndex, ticker) {
    state.compare[slotIndex - 1] = ticker;
    if (slotIndex === 1) document.getElementById("compare-add-button").hidden = false;
    renderCompare();
  }

  function renderCompare() {
    var emptyEl = document.getElementById("compare-empty");
    var cardsEl = document.getElementById("compare-cards");
    var stocks = universeStocks();
    var selected = state.compare.filter(Boolean);

    if (!selected.length) {
      emptyEl.hidden = false;
      cardsEl.innerHTML = "";
      cardsEl.classList.remove("split");
      return;
    }
    emptyEl.hidden = true;
    cardsEl.classList.toggle("split", selected.length === 2);
    cardsEl.innerHTML = selected.map(function (ticker) {
      return renderCompareCard(ticker, stocks[ticker]);
    }).join("");
  }

  var PERIOD_LABELS = { d3: "3 DAYS", d7: "7 DAYS", d14: "14 DAYS", m1: "1 MONTH", m6: "6 MONTHS", y1: "1 YEAR" };

  function renderCompareCard(ticker, s) {
    if (!s) return "";
    var lastChangeIsPos = (s.last.day_change_pct || 0) >= 0;
    var periodsHtml = ["d3", "d7", "d14", "m1", "m6", "y1"].map(function (key) {
      var p = s[key];
      if (!p) {
        return '<div class="compare-period"><div class="compare-period-label">' + PERIOD_LABELS[key] +
          '</div><span class="stat-cell-empty">–</span></div>';
      }
      var isPos = p.change_pct_vs_last >= 0;
      return (
        '<div class="compare-period">' +
          '<div class="compare-period-label">' + PERIOD_LABELS[key] + "</div>" +
          '<div class="compare-period-close">₹' + formatThousands(p.close, 2) + "</div>" +
          '<div class="compare-period-change ' + (isPos ? "pos" : "neg") + '">' +
            (isPos ? "▲ " : "▼ ") + Math.abs(p.change_pct_vs_last).toFixed(2) + "%</div>" +
        "</div>"
      );
    }).join("");

    return (
      '<div class="compare-card">' +
        '<div class="compare-card-header">' +
          "<div>" +
            '<div class="compare-card-name">' + escapeHtml(s.company_name) + "</div>" +
            '<div class="compare-card-sub">' + escapeHtml(s.sector) + " · " + escapeHtml(ticker) + "</div>" +
          "</div>" +
          '<div class="compare-card-last">₹' + formatThousands(s.last.close, 2) +
            '<div class="compare-card-sub ' + (lastChangeIsPos ? "pos" : "neg") + '">' +
              (lastChangeIsPos ? "▲ " : "▼ ") + Math.abs(s.last.day_change_pct || 0).toFixed(2) + "% today</div>" +
          "</div>" +
        "</div>" +
        '<div class="compare-periods">' + periodsHtml + "</div>" +
      "</div>"
    );
  }

  // ==================================================================
  // Helpers
  // ==================================================================

  function formatThousands(n, decimals) {
    if (n == null) return "–";
    return Number(n).toLocaleString("en-IN", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  }

  var MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

  function formatDate(isoDate) {
    if (!isoDate) return "–";
    var d = new Date(isoDate + "T00:00:00");
    return d.getDate() + " " + MONTHS[d.getMonth()] + " " + d.getFullYear();
  }

  function formatDateShort(isoDate) {
    if (!isoDate) return "–";
    var d = new Date(isoDate + "T00:00:00");
    return d.getDate() + " " + MONTHS[d.getMonth()];
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  renderTicker();
  renderFootnote();
  goToSection("home");
})();
