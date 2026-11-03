/* Lost School Days — figures computed in the browser from history.json (past), data.json (coming days)
   and places.json (every city and town). */
(() => {
  const REGION_ORDER = [
    "NCR", "CAR", "Ilocos Region", "Cagayan Valley", "Central Luzon", "CALABARZON", "MIMAROPA",
    "Bicol Region", "Western Visayas", "Negros Island Region", "Central Visayas", "Eastern Visayas",
    "Zamboanga Peninsula", "Northern Mindanao", "Davao Region", "SOCCSKSARGEN", "Caraga", "BARMM",
  ];
  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const MONTH_FULL = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  const LEVEL_LABELS = ["Preschool", "Elementary", "Junior High", "Senior High", "College"];
  const LEVEL_KEYS = ["preschool", "elementary", "junior_high", "senior_high", "college"];
  const ACTIVE = new Set(["suspended", "online_or_modular", "opening_postponed"]);
  const SHOWN = new Set([...ACTIVE, "lifted"]);
  const PROV_SHOWN = 15;
  // Causes, grouped so the month-by-month chart has four clear colours
  const GROUPS = [
    ["Typhoons and storms", ["Typhoon / tropical storm"], "--g1"],
    ["Monsoon and other rain", ["Monsoon rains", "Low pressure area / shear line", "Rain and flooding"], "--g2"],
    ["Extreme heat", ["Extreme heat"], "--g3"],
    ["Other", ["Earthquake", "Volcanic activity", "Transport strike", "Not stated"], "--g4"],
  ];

  // ── Helpers ───────────────────────────────────────────────────────────────
  const $ = (s) => document.querySelector(s);
  const el = (tag, props = {}, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else if (k === "style") n.style.cssText = v;
      else n.setAttribute(k, v);
    }
    n.append(...kids.filter((k) => k !== null && k !== undefined && k !== false));
    return n;
  };
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const fmt = (n) => n.toLocaleString("en-US");
  const pct = (a, b) => (b ? (100 * a) / b : 0);
  const pctText = (a, b) => { const p = pct(a, b); return `${p > 0 && p < 1 ? p.toFixed(1) : Math.round(p)}%`; };
  const plural = (n, w, ws = `${w}s`) => `${fmt(n)} ${n === 1 ? w : ws}`;
  const regionRank = (r) => REGION_ORDER.indexOf(r) + 1 || 99;
  const phToday = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Manila" }).format(new Date());
  const addDays = (iso, n) => { const d = new Date(`${iso}T00:00:00Z`); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
  const dayLabel = (iso, opts) => new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", { timeZone: "UTC", ...opts });
  const timeText = (iso) => new Date(iso).toLocaleString("en-US", { timeZone: "Asia/Manila", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  const unique = (xs) => [...new Set(xs)];
  const countBy = (xs, f) => xs.reduce((m, x) => m.set(f(x), (m.get(f(x)) || 0) + 1), new Map());
  // Same place matching as the scraper (place_key in trial_pipeline.py)
  const ALIASES = { montalban: "rodriguez", "general santos": "gensan" };
  const placeKey = (s) => {
    const k = (s || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[‘’“”'"`]/g, "")
      .replace(/[^a-z0-9 ]/g, " ").replace(/\s+/g, " ").trim()
      .replace(/^city of /, "").replace(/ city$/, "").replace(/^(municipality|town) of /, "").replace(/ (town|municipality)$/, "");
    return ALIASES[k] || k;
  };

  const params = new URLSearchParams(location.search);
  const state = { year: "all", region: "", metric: "days", provSort: "pct", provDir: -1, provAll: false, date: "", town: null };
  let H = null, LIVE = null, RECS = [], YEARS = [], PLACES = [], uniqueNames = new Set();
  let HOLIDAYS = new Set(), BREAKS = [];
  const charts = {};

  // ── Load ──────────────────────────────────────────────────────────────────
  const getJSON = (url) => fetch(url, { cache: "no-store" }).then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); });
  const loadLive = () => {
    const urls = [window.LIVE_DATA_URL, "data.json"].filter(Boolean);
    const attempt = (i) => getJSON(`${urls[i]}?t=${Date.now()}`).catch((e) => (i + 1 < urls.length ? attempt(i + 1) : Promise.reject(e)));
    return attempt(0);
  };

  Promise.all([getJSON("history.json"), loadLive().catch(() => null), getJSON("places.json").catch(() => ({ places: [] }))])
    .then(([h, live, places]) => {
      H = h;
      LIVE = live;
      RECS = h.records.map(([date, region, province, city, scope, mask, sector, inPerson, partDay]) => ({
        date, region, province, city, scope, mask, sector, inPerson: !!inPerson, partDay: !!partDay,
        year: date.slice(0, 4), month: date.slice(0, 7), cause: h.days[date].cause,
      }));
      YEARS = unique(RECS.map((r) => r.year)).sort();
      HOLIDAYS = new Set((h.calendar?.holidays || []).map(([d]) => d));
      BREAKS = (h.calendar?.breaks || []).map(([a, b]) => [a, b]);
      PLACES = places.places.map(([region, province, name, isCity, independent]) =>
        ({ region, province, name, isCity: !!isCity, independent: !!independent, label: `${name}, ${province}` }));
      const counts = countBy(PLACES, (p) => placeKey(p.name));
      uniqueNames = new Set([...counts].filter(([, n]) => n === 1).map(([k]) => k));
      const wantDate = params.get("date");
      if (/^\d{4}-\d{2}-\d{2}$/.test(wantDate || "")) state.date = wantDate;
      state.town = PLACES.find((p) => p.label.toLowerCase() === (params.get("town") || "").toLowerCase()) || null;
      buildControls();
      render();
      setInterval(() => loadLive().then((l) => { LIVE = l; renderBreakdownLive(); }).catch(() => {}), 5 * 60 * 1000);
    })
    .catch((e) => { $("#lede").textContent = `Couldn't load the data (${e.message}).`; });

  // ── School days ───────────────────────────────────────────────────────────
  function isSchoolDay(iso) {
    const wd = new Date(`${iso}T00:00:00Z`).getUTCDay();
    return wd !== 0 && wd !== 6 && !HOLIDAYS.has(iso) && !BREAKS.some(([a, b]) => iso >= a && iso <= b);
  }
  const lastDate = () => H.covered[H.covered.length - 1][1];
  function periodRange() {
    if (state.year === "all") return [H.covered[0][0], lastDate()];
    const end = `${state.year}-12-31`;
    return [`${state.year}-01-01`, end < lastDate() ? end : lastDate()];
  }
  const schoolDayCache = new Map();
  function schoolDays() {
    const [a, b] = periodRange();
    const key = `${a}|${b}`;
    if (!schoolDayCache.has(key)) {
      let n = 0;
      for (let d = a; d <= b; d = addDays(d, 1)) if (isSchoolDay(d)) n += 1;
      schoolDayCache.set(key, n);
    }
    return schoolDayCache.get(key);
  }
  const lostDays = (rs) => unique(rs.map((r) => r.date)).filter(isSchoolDay);

  // ── Filters ───────────────────────────────────────────────────────────────
  function yearLabel(y) {
    const end = lastDate();
    return y === end.slice(0, 4) && !end.endsWith("-12-31") ? `${y} (to ${dayLabel(end, { month: "short", day: "numeric" })})` : y;
  }
  function seg(root, options, current, onPick) {
    root.replaceChildren(...options.map(([v, label]) => {
      const b = el("button", { type: "button", "aria-pressed": String(v === current), text: label });
      b.addEventListener("click", () => onPick(v));
      return b;
    }));
  }
  const inRegion = (r) => !state.region || r.region === state.region;
  const filtered = () => RECS.filter((r) => (state.year === "all" || r.year === state.year) && inRegion(r));
  const lguOnly = (rs) => rs.filter((r) => r.scope !== "partial");
  const where = () => (state.region ? ` in ${state.region}` : "");
  const period = () => {
    const [a, b] = periodRange();
    return `${dayLabel(a, { month: "short", day: "numeric", year: "numeric" })} to ${dayLabel(b, { month: "short", day: "numeric", year: "numeric" })}`;
  };

  function buildControls() {
    const drawYear = () => seg($("#f-year"), [["all", `All (${YEARS[0]}–${YEARS[YEARS.length - 1]})`], ...YEARS.map((y) => [y, yearLabel(y)])],
      state.year, (v) => { state.year = v; drawYear(); render(); });
    drawYear();
    const regions = unique(RECS.map((r) => r.region).filter(Boolean)).sort((a, b) => regionRank(a) - regionRank(b));
    $("#f-region").replaceChildren(el("option", { value: "", text: "All regions" }), ...regions.map((r) => el("option", { value: r, text: r })));
    $("#f-region").addEventListener("change", (e) => { state.region = e.target.value; render(); });
    const drawMetric = () => seg($("#f-metric"), [["days", "School days lost"], ["lgu", "LGU suspensions"]], state.metric,
      (v) => { state.metric = v; drawMetric(); renderTimeline(filtered()); });
    drawMetric();

    $("#town-list").replaceChildren(...PLACES.map((p) => el("option", { value: p.label })));
    const pickTown = () => {
      const v = $("#town").value.trim().toLowerCase();
      const found = PLACES.find((p) => p.label.toLowerCase() === v);
      if (found || !v) { state.town = found || null; renderTown(); }
    };
    $("#town").addEventListener("change", pickTown);
    $("#town").addEventListener("input", () => { if (PLACES.some((p) => p.label === $("#town").value)) pickTown(); });
    $("#town-clear").addEventListener("click", () => { $("#town").value = ""; state.town = null; renderTown(); $("#town").focus(); });
    if (state.town) $("#town").value = state.town.label;

    $("#prov-more").addEventListener("click", () => { state.provAll = !state.provAll; renderProvinces(filtered()); });
    $("#f-date").addEventListener("change", (e) => { state.date = e.target.value; renderByDate(); });
    $("#dl-prov").addEventListener("click", () => downloadCsv("school_days_lost_by_province", provinceRows(filtered()).map((p) => ({
      province: p.province, region: p.region, school_days_lost: p.days, school_days_in_period: schoolDays(),
      percent_of_school_days: pct(p.days, schoolDays()).toFixed(1), whole_province_days: p.wideDays,
      cities_and_towns_affected: p.towns, main_cause: p.cause }))));
    $("#dl-date").addEventListener("click", () => downloadCsv(`class_suspensions_${state.date}`, rowsFor(state.date).map((r) => ({
      date: r.date, region: r.region, province: r.province, city_or_town: placeName(r), levels_suspended: levelsText(r.mask),
      details: r.notes.join("; "), confidence: r.conf, confidence_reason: r.why, outlets: r.outlets.join("; "), source: r.url || "" }))));
    $("#dl-all").addEventListener("click", () => downloadCsv("class_suspensions", filtered().map((r) => ({
      date: r.date, school_day: isSchoolDay(r.date) ? "yes" : "no", region: r.region, province: r.province,
      city_or_town: r.city || "(whole province)", scope: r.scope, levels: levelsText(r.mask),
      public_private: r.sector || "not stated", face_to_face_only: r.inPerson ? "yes" : "no", part_of_day: r.partDay ? "yes" : "no",
      cause: r.cause, storm: H.days[r.date].storms.join("; "), source: H.days[r.date].url }))));
  }

  function render() {
    const rs = filtered();
    renderKpis(rs);
    renderFindings(rs);
    renderTimeline(rs);
    renderCauses(rs);
    renderTown();
    renderProvinces(rs);
    renderBreakdownLive();
    $("#cal-count").textContent = `${fmt(schoolDays())} school days from ${period()}`;
    $("#footer").textContent = `Past data: ${fmt(RECS.length)} records on ${unique(RECS.map((r) => r.date)).length} days, to ${dayLabel(lastDate(), { month: "long", day: "numeric", year: "numeric" })}. Source: ${H.source}; coming days from the live news check.`;
  }

  // ── Overall: key figures ──────────────────────────────────────────────────
  function provinceRows(rs) {
    const by = new Map();
    rs.filter((r) => r.province).forEach((r) => { if (!by.has(r.province)) by.set(r.province, []); by.get(r.province).push(r); });
    return [...by].map(([province, list]) => {
      const days = lostDays(list);
      const causes = countBy(days, (d) => H.days[d].cause);
      return {
        province, region: list[0].region, days: days.length, pct: pct(days.length, schoolDays()),
        wideDays: lostDays(list.filter((r) => r.scope === "province")).length,
        towns: unique(lguOnly(list).filter((r) => r.city).map((r) => r.city.toLowerCase())).length,
        cause: causes.size ? [...causes].sort((a, b) => b[1] - a[1])[0][0] : "—",
      };
    }).filter((p) => p.days > 0);
  }

  function renderKpis(rs) {
    const lost = lostDays(rs).length;
    const total = schoolDays();
    const provs = provinceRows(rs).sort((a, b) => b.days - a.days);
    const kpi = (v, l, s) => el("div", { class: "kpi" }, el("p", { class: "v", text: v }), el("p", { class: "l", text: l }), el("p", { class: "s", text: s }));
    $("#kpis").replaceChildren(
      kpi(pctText(lost, total), "of school days had a suspension", `${fmt(lost)} of ${fmt(total)} school days, somewhere${where() || " in the country"}`),
      kpi(fmt(lguOnly(rs).length), "LGU suspensions", "one city, town or province suspending classes for one day"),
      kpi(fmt(provs.length), "Provinces affected", state.region ? `in ${state.region}` : "including Metro Manila"),
      provs.length ? kpi(pctText(provs[0].days, total), `Hardest hit: ${provs[0].province}`, `${fmt(provs[0].days)} of ${fmt(total)} school days lost`)
        : kpi("—", "Hardest hit", ""),
    );
  }

  // ── Overall: findings (merges the old events and "kind of suspension" panels) ──
  function stormEvents(rs) {
    const events = [];
    unique(rs.map((r) => r.date)).sort().forEach((d) => {
      const storm = H.days[d].storms[0];
      if (!storm) return;
      const e = events.find((x) => x.storm === storm && (new Date(d) - new Date(x.dates[x.dates.length - 1])) / 864e5 <= 7);
      if (e) e.dates.push(d); else events.push({ storm, dates: [d] });
    });
    events.forEach((e) => { const s = new Set(e.dates); e.lgu = lguOnly(rs.filter((r) => s.has(r.date))).length; });
    return events.sort((a, b) => b.lgu - a.lgu);
  }

  function renderFindings(rs) {
    const out = [];
    const lgu = lguOnly(rs);
    const lost = lostDays(rs);
    const total = schoolDays();
    if (!lost.length) {
      $("#findings").replaceChildren(el("li", { text: `No class suspensions on school days${where()} from ${period()}.` }));
      return;
    }
    const b = (t) => el("b", { text: t });
    out.push(el("li", {}, "From ", period(), where(), ", classes were suspended somewhere on ", b(`${fmt(lost.length)} of ${fmt(total)} school days (${pctText(lost.length, total)})`),
      `, adding up to ${fmt(lgu.length)} LGU suspensions.`,
      state.year === "all" && YEARS.length > 1 ? ` By year: ${YEARS.map((y) => `${yearLabel(y)}: ${fmt(lguOnly(rs.filter((r) => r.year === y)).length)}`).join("; ")}.` : ""));

    const byCause = [...countBy(lost, (d) => H.days[d].cause)].sort((a, c) => c[1] - a[1]);
    const heat = lost.filter((d) => H.days[d].cause === "Extreme heat");
    out.push(el("li", {}, b(byCause[0][0]), ` caused the most lost days (${byCause[0][1]})`,
      byCause[1] ? `, then ${byCause[1][0].toLowerCase()} (${byCause[1][1]})` : "", ".",
      heat.length ? ` Extreme heat cost ${plural(heat.length, "day")}, all in ${unique(heat.map((d) => `${MONTH_FULL[+d.slice(5, 7) - 1]} ${d.slice(0, 4)}`)).join(" and ")}, when classes were still in session.` : ""));

    const provs = provinceRows(rs).sort((a, c) => c.days - a.days).slice(0, 3);
    out.push(el("li", {}, "Hardest hit: ", ...provs.flatMap((p, i) => [i ? (i === provs.length - 1 ? " and " : ", ") : "", b(p.province), ` (${pctText(p.days, total)} of school days)`]), "."));

    const julNov = lgu.filter((r) => { const m = +r.date.slice(5, 7); return m >= 7 && m <= 11; }).length;
    const worst = [...countBy(lgu, (r) => r.month)].sort((a, c) => c[1] - a[1])[0];
    out.push(el("li", {}, b(pctText(julNov, lgu.length)), " of suspensions fell from July to November, the first months of the school year",
      worst ? `; the worst month was ${MONTH_FULL[+worst[0].slice(5) - 1]} ${worst[0].slice(0, 4)} (${fmt(worst[1])} LGU suspensions)` : "", "."));

    const events = stormEvents(rs).slice(0, 3);
    if (events.length) {
      out.push(el("li", {}, "Biggest storms: ", ...events.flatMap((e, i) => [i ? "; " : "", b(e.storm),
        ` (${MON[+e.dates[0].slice(5, 7) - 1]} ${e.dates[0].slice(0, 4)}, ${fmt(e.lgu)} LGU suspensions)`]), "."));
    }

    out.push(el("li", {}, b(pctText(lgu.filter((r) => r.mask === 31).length, lgu.length)), " of suspensions covered every level, preschool to college; ",
      b(pctText(lgu.filter((r) => r.inPerson).length, lgu.length)), " stopped face-to-face classes only, with learning moved to modular or online."));
    $("#findings").replaceChildren(...out);
  }

  // ── Overall: charts ───────────────────────────────────────────────────────
  const SHORT_CAUSE = { "Typhoon / tropical storm": "Typhoon or storm", "Low pressure area / shear line": "Low pressure" };
  const groupOf = (cause) => GROUPS.find(([, causes]) => causes.includes(cause)) || GROUPS[3];
  // Prints each bar's total (vertical stacks) or value (horizontal bars) next to it
  const valueLabels = {
    id: "valueLabels",
    afterDatasetsDraw(chart) {
      const { ctx, scales: { x, y } } = chart;
      const horizontal = chart.options.indexAxis === "y";
      const n = chart.data.labels.length;
      ctx.save();
      ctx.font = `600 12px ${css("--font-body")}`;
      ctx.fillStyle = css("--ink");
      for (let i = 0; i < n; i++) {
        const total = chart.data.datasets.reduce((s, ds, k) => s + (chart.isDatasetVisible(k) ? ds.data[i] || 0 : 0), 0);
        if (!total) continue;
        if (horizontal) {
          ctx.textAlign = "left"; ctx.textBaseline = "middle";
          ctx.fillText(fmt(total), x.getPixelForValue(total) + 6, y.getPixelForValue(i));
        } else {
          ctx.textAlign = "center"; ctx.textBaseline = "bottom";
          ctx.fillText(fmt(total), x.getPixelForValue(i), y.getPixelForValue(total) - 4);
        }
      }
      ctx.restore();
    },
  };
  function draw(id, config) {
    Chart.defaults.font.family = css("--font-body");
    Chart.defaults.font.size = 13;
    Chart.defaults.color = css("--muted");
    if (charts[id]) charts[id].destroy();
    config.options.animation = false;  // labels and bars appear together
    charts[id] = new Chart(document.getElementById(id), { ...config, plugins: [valueLabels] });
  }

  function monthsInView() {
    const [a, b] = periodRange();
    const out = [];
    for (let m = a.slice(0, 7); m <= b.slice(0, 7);) {
      out.push(m);
      const [y, mm] = m.split("-").map(Number);
      m = mm === 12 ? `${y + 1}-01` : `${y}-${String(mm + 1).padStart(2, "0")}`;
    }
    return out;
  }

  function renderTimeline(rs) {
    const months = monthsInView();
    const lgu = lguOnly(rs);
    const value = (month, causes) => (state.metric === "lgu"
      ? lgu.filter((r) => r.month === month && causes.includes(r.cause)).length
      : lostDays(rs.filter((r) => r.month === month && causes.includes(r.cause))).length);
    $("#timeline-sub").textContent = state.metric === "lgu"
      ? `LGU suspensions each month${where()}, by cause. The number on each bar is the month's total.`
      : `School days with a suspension somewhere${where()}, each month, by cause. The number on each bar is the month's total.`;
    const datasets = GROUPS.map(([label, causes, color]) => ({ label, data: months.map((m) => value(m, causes)),
      backgroundColor: css(color), borderWidth: 0, borderRadius: 3, maxBarThickness: 34 })).filter((ds) => ds.data.some(Boolean));
    draw("c-timeline", {
      type: "bar",
      data: { labels: months.map((m) => `${MON[+m.slice(5) - 1]} '${m.slice(2, 4)}`), datasets },
      options: {
        responsive: true, maintainAspectRatio: false, layout: { padding: { top: 22 } },
        plugins: {
          legend: { position: "top", align: "start", labels: { boxWidth: 14, boxHeight: 14, padding: 16, color: css("--ink"), font: { size: 13 } } },
          tooltip: { mode: "index", filter: (i) => i.raw > 0 },
        },
        scales: {
          x: { stacked: true, grid: { display: false }, ticks: { autoSkip: true, autoSkipPadding: 6, maxRotation: 0, minRotation: 0, font: { size: 12 } } },
          y: { stacked: true, beginAtZero: true, grace: "8%", ticks: { precision: 0 }, grid: { color: css("--line") }, border: { display: false } },
        },
      },
    });
  }

  function renderCauses(rs) {
    const lost = lostDays(rs);
    const rows = [...countBy(lost, (d) => H.days[d].cause)].sort((a, b) => b[1] - a[1]);
    draw("c-cause", {
      type: "bar",
      data: { labels: rows.map(([c]) => SHORT_CAUSE[c] || c), datasets: [{ label: "School days lost", data: rows.map(([, n]) => n),
        backgroundColor: rows.map(([c]) => css(groupOf(c)[2])), borderRadius: 3, maxBarThickness: 26 }] },
      options: {
        indexAxis: "y", responsive: true, maintainAspectRatio: false, layout: { padding: { right: 36 } },
        plugins: { legend: { display: false } },
        scales: { x: { beginAtZero: true, ticks: { precision: 0 }, grid: { color: css("--line") }, border: { display: false } },
          y: { grid: { display: false }, ticks: { color: css("--ink"), font: { size: 13 } } } },
      },
    });
  }

  // ── Breakdown: a city or town ─────────────────────────────────────────────
  // A record applies to a town if it names it, or covers it (province-wide, except independent
  // cities outside Metro Manila; region-wide; nationwide)
  function covers(r, p) {
    const prov = placeKey(r.province), city = placeKey(r.city === "all cities" ? "" : r.city);
    if (r.scope === "city") return city === placeKey(p.name) && (prov === placeKey(p.province) || !r.province || uniqueNames.has(placeKey(p.name)));
    if (r.scope === "province") {
      if (p.isCity && prov === placeKey(p.name)) return true;
      return prov === placeKey(p.province) && (!p.independent || p.province === "Metro Manila");
    }
    if (r.scope === "region") return r.region === p.region;
    return r.scope === "national";
  }

  function renderTown() {
    const p = state.town;
    $("#town-clear").hidden = !p;
    try { const u = new URL(location.href); p ? u.searchParams.set("town", p.label) : u.searchParams.delete("town"); history.replaceState(null, "", u); } catch { /* sandboxed */ }
    if (!p) { $("#town-result").replaceChildren(); return; }
    const total = schoolDays();
    const [a, b] = periodRange();
    const days = lostDays(RECS.filter((r) => r.date >= a && r.date <= b && covers(r, p))).sort().reverse();
    const today = phToday();
    const live = LIVE ? (LIVE.records || []).filter((r) => SHOWN.has(r.status) && covers(r, p)) : [];
    const status = (d) => {
      const rs = live.filter((r) => r.date === d);
      const active = rs.find((r) => ACTIVE.has(r.status));
      if (active) return el("span", { class: "pill on" }, `${active.status === "online_or_modular" ? "Moved online" : "Suspended"}: ${levelsText(maskOf(active.levels))}`);
      if (rs.some((r) => r.status === "lifted")) return el("span", { class: "pill lifted", text: "Suspension lifted" });
      return el("span", { class: "pill", text: "No suspension announced" });
    };
    $("#town-result").replaceChildren(el("div", { class: "town-card" },
      el("h3", {}, p.name, el("span", { text: ` ${p.province} · ${p.region}` })),
      el("div", { class: "town-now" },
        el("p", {}, el("b", { text: "Today " }), status(today)),
        el("p", {}, el("b", { text: "Tomorrow " }), status(addDays(today, 1)))),
      el("p", { class: "town-lost" }, el("b", { text: `${pctText(days.length, total)} of school days lost` }),
        ` · ${plural(days.length, "school day")} of ${fmt(total)} from ${period()}, counting its own suspensions and province-wide ones` +
        (p.independent && p.province !== "Metro Manila" ? " (as an independent city, it isn't covered by the province's)" : "") + "."),
      days.length ? el("div", { class: "chips", role: "list" }, ...days.map((d) => {
        const c = el("button", { type: "button", class: "chip", role: "listitem", text: dayLabel(d, { month: "short", day: "numeric", year: "numeric" }) });
        c.addEventListener("click", () => { state.date = d; renderByDate(); $("#t-date").scrollIntoView({ behavior: "smooth", block: "start" }); });
        return c;
      })) : null));
  }

  // ── Breakdown: where school days are lost ─────────────────────────────────
  function renderProvinces(rs) {
    const total = schoolDays();
    $("#prov-sub").textContent = `Share of the ${fmt(total)} school days${where()} from ${period()} on which at least one LGU in the province suspended classes.`;
    const rows = provinceRows(rs);
    const key = state.provSort;
    rows.sort((a, b) => (typeof a[key] === "string" ? a[key].localeCompare(b[key]) * -state.provDir : (a[key] - b[key]) * state.provDir) || b.days - a.days);
    const max = Math.max(1, ...rows.map((r) => r.pct));
    const cols = [["province", "Province", false], ["pct", "% of school days lost", true], ["days", "School days lost", true],
      ["wideDays", "Whole province", true], ["towns", "Cities & towns", true], ["cause", "Main cause", false]];
    const head = el("tr", {}, el("th", { class: "rank", text: "#" }), ...cols.map(([k, label, num]) => {
      const th = el("th", { class: num ? "num" : "", scope: "col", "aria-sort": state.provSort === k ? (state.provDir < 0 ? "descending" : "ascending") : "none" });
      const btn = el("button", { type: "button", class: "sort", text: label });
      btn.addEventListener("click", () => { state.provDir = state.provSort === k ? -state.provDir : -1; state.provSort = k; renderProvinces(filtered()); });
      th.append(btn);
      return th;
    }));
    const shown = state.provAll ? rows : rows.slice(0, PROV_SHOWN);
    const body = shown.map((r, i) => el("tr", {},
      el("td", { class: "rank", text: String(i + 1) }),
      el("td", {}, r.province, el("span", { class: "sub", text: r.region })),
      el("td", { class: "num pct-cell" }, el("span", { class: "bar", style: `width:${Math.round((80 * r.pct) / max)}px` }), el("b", { text: pctText(r.days, total) })),
      el("td", { class: "num", text: `${r.days} of ${total}` }),
      el("td", { class: "num", text: String(r.wideDays) }),
      el("td", { class: "num", text: String(r.towns) }),
      el("td", {}, el("span", { class: "cause-dot", style: `background:${css(groupOf(r.cause)[2])}` }), r.cause)));
    $("#t-prov").replaceChildren(el("thead", {}, head), el("tbody", {}, ...body));
    $("#prov-more").hidden = rows.length <= PROV_SHOWN;
    $("#prov-more").textContent = state.provAll ? "Show top 15" : `Show all ${rows.length} provinces`;
  }

  // ── Breakdown: every place, by date ───────────────────────────────────────
  const maskOf = (levels) => (levels ? LEVEL_KEYS.reduce((m, k, i) => m | (levels[k] ? 1 << i : 0), 0) : -1);
  function levelsText(mask) {
    if (mask < 0 || mask === 0) return "Not stated";
    if (mask === 31) return "All levels";
    const idx = LEVEL_LABELS.map((_, i) => i).filter((i) => mask & (1 << i));
    if (idx.length === 1) return `${LEVEL_LABELS[idx[0]]} only`;
    return idx[idx.length - 1] - idx[0] === idx.length - 1 ? `${LEVEL_LABELS[idx[0]]} to ${LEVEL_LABELS[idx[idx.length - 1]]}`
      : idx.map((i) => LEVEL_LABELS[i]).join(", ");
  }
  const SECTOR_TEXT = { public: "public schools only", private: "private schools only" };

  function liveRow(r) {
    const notes = [];
    if (r.status === "online_or_modular") notes.push("moved online / modular");
    if (r.status === "opening_postponed") notes.push("school opening postponed");
    if (r.status === "lifted") notes.push("suspension lifted");
    if ((r.notes || []).includes("In-person only")) notes.push("face-to-face classes only");
    const from = (r.notes || []).find((n) => n.startsWith("From"));
    if (from) notes.push(from.toLowerCase());
    const sector = { "Public only": "public", "Private only": "private" }[r.sector];
    if (sector) notes.push(SECTOR_TEXT[sector]);
    return { date: r.date, region: r.region || "", province: r.province || "", city: r.city, scope: r.scope,
      mask: maskOf(r.levels), notes, lifted: r.status === "lifted", conf: r.confidence, why: r.confidence_reason,
      url: r.sources[0] && r.sources[0].url, outlets: unique(r.sources.map((x) => x.outlet)) };
  }
  function pastRow(r) {
    const notes = [];
    if (r.inPerson) notes.push("face-to-face classes only");
    if (r.partDay) notes.push("part of the day");
    if (SECTOR_TEXT[r.sector]) notes.push(SECTOR_TEXT[r.sector]);
    let conf = 3; const why = ["1 outlet (Rappler)"];
    if (r.mask < 0) { conf -= 1; why.push("levels not stated"); }
    if (!r.sector) { conf -= 1; why.push("public/private not stated"); }
    return { date: r.date, region: r.region, province: r.province,
      city: r.scope === "province" ? "all cities" : r.city, scope: r.scope === "partial" ? "schools" : r.scope,
      mask: r.mask, notes, lifted: false, conf: Math.max(1, conf), why: why.join("; "),
      url: H.days[r.date].url, outlets: ["Rappler"] };
  }
  const liveRows = () => (LIVE ? (LIVE.records || []).filter((r) => SHOWN.has(r.status)).map(liveRow) : []);
  function rowsFor(d) {
    const live = liveRows().filter((r) => r.date === d);
    const rows = live.length || (LIVE && d >= addDays(LIVE.today, -1)) ? live : filtered().filter((r) => r.date === d).map(pastRow);
    return rows.filter(inRegion);
  }
  function dateOptions() {
    const today = phToday();
    const upcoming = unique([today, addDays(today, 1), ...liveRows().filter(inRegion).map((r) => r.date).filter((d) => d >= today)]).sort();
    const past = unique(filtered().map((r) => r.date)).filter((d) => d < today).sort().reverse();
    return { upcoming, past };
  }

  function renderByDate() {
    const opts = dateOptions();
    if (!state.date || ![...opts.upcoming, ...opts.past].includes(state.date)) {
      state.date = opts.upcoming.find((d) => rowsFor(d).length) || opts.past[0] || opts.upcoming[0];
    }
    const today = phToday();
    const label = (d) => {
      const n = rowsFor(d).length;
      const word = d === today ? "Today, " : d === addDays(today, 1) ? "Tomorrow, " : "";
      return `${word}${dayLabel(d, { weekday: "short", month: "short", day: "numeric", year: "numeric" })} — ${n ? plural(n, "place") : "none announced"}`;
    };
    const byMonth = new Map();
    opts.past.forEach((d) => { const m = d.slice(0, 7); if (!byMonth.has(m)) byMonth.set(m, []); byMonth.get(m).push(d); });
    $("#f-date").replaceChildren(
      el("optgroup", { label: "Today and coming days" }, ...opts.upcoming.map((d) => el("option", { value: d, text: label(d) }))),
      ...[...byMonth].map(([m, ds]) => el("optgroup", { label: `${MONTH_FULL[+m.slice(5) - 1]} ${m.slice(0, 4)}` },
        ...ds.map((d) => el("option", { value: d, text: label(d) })))));
    $("#f-date").value = state.date;
    try { const u = new URL(location.href); u.searchParams.set("date", state.date); history.replaceState(null, "", u); } catch { /* sandboxed */ }
    renderNowBoxes(opts.upcoming.slice(0, 2));
    renderDateTable();
  }

  function renderNowBoxes(days) {
    const today = phToday();
    $("#now-days").replaceChildren(...days.map((d) => {
      const rs = rowsFor(d);
      const areas = unique(rs.map((r) => (r.scope === "national" ? "Nationwide" : r.scope === "region" ? r.region : r.province)).filter(Boolean));
      const b = el("button", { type: "button", class: `now-day${rs.length ? " has" : ""}`, "aria-pressed": String(state.date === d) },
        el("span", { class: "when", text: `${d === today ? "Today" : "Tomorrow"} · ${dayLabel(d, { month: "short", day: "numeric" })}` }),
        el("span", { class: "n", text: rs.length ? plural(rs.length, "place") : "None announced" }),
        el("span", { class: "where", text: rs.length ? `${areas.slice(0, 6).join(", ")}${areas.length > 6 ? ` and ${areas.length - 6} more` : ""}` : "No suspension in the news so far." }));
      b.addEventListener("click", () => { state.date = d; renderByDate(); });
      return b;
    }));
  }

  function placeName(r) {
    if (r.scope === "national") return "Nationwide";
    if (r.scope === "region") return "Whole region";
    if (r.scope === "province" || r.city === "all cities") return r.province === "Metro Manila" ? "All of Metro Manila" : "Whole province";
    if (r.scope === "schools" || !r.city) return r.city ? `Some schools: ${r.city}` : "Some schools";
    return r.city;
  }
  const pips = (n) => el("span", { class: "pips", "aria-hidden": "true" }, ...[1, 2, 3, 4, 5].map((i) => el("i", { class: i <= n ? "f" : "" })));

  function renderDateTable() {
    const d = state.date;
    const rows = rowsFor(d);
    const groups = new Map();
    rows.forEach((r) => {
      const g = r.scope === "national" ? "Nationwide" : r.scope === "region" ? `${r.region} (whole region)` : r.province || "Province not stated";
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push(r);
    });
    const order = [...groups.keys()].sort((a, b) => (a === "Nationwide" ? -1 : b === "Nationwide" ? 1 : 0)
      || (regionRank(groups.get(a)[0].region) - regionRank(groups.get(b)[0].region)) || a.localeCompare(b));
    const rank = (r) => ({ national: 0, region: 1, province: 2, city: 3, schools: 4 }[r.scope] ?? 5);
    const fromLive = rows.length && LIVE && d >= addDays(LIVE.today, -1);
    const long = dayLabel(d, { weekday: "long", month: "long", day: "numeric", year: "numeric" });
    $("#date-sum").textContent = rows.length
      ? `${long}: ${plural(rows.length, "place")} in ${plural(groups.size, "province")}${where()}. ${fromLive ? "From the live check." : "From Rappler's roundup."}${isSchoolDay(d) ? "" : " Not a school day."}`
      : `${long}: no suspensions${where()} ${d >= phToday() ? "announced so far" : "recorded"}.`;
    $("#dl-date").hidden = !rows.length;
    const head = el("thead", {}, el("tr", {},
      el("th", { scope: "col", text: "Province" }), el("th", { scope: "col", text: "City / town" }),
      el("th", { scope: "col", text: "Levels suspended" }), el("th", { scope: "col", text: "Confidence" })));
    if (!rows.length) {
      $("#t-date").replaceChildren(head, el("tbody", {}, el("tr", {}, el("td", { colspan: "4", class: "empty-cell", text: "Nothing to show for this date." }))));
      return;
    }
    const body = [];
    order.forEach((g) => {
      const list = groups.get(g).sort((a, b) => rank(a) - rank(b) || placeName(a).localeCompare(placeName(b)));
      list.forEach((r, i) => {
        const cells = [];
        if (i === 0) {
          cells.push(el("th", { scope: "rowgroup", rowspan: String(list.length), class: "prov-cell" }, g,
            r.region && r.scope !== "region" && r.scope !== "national" ? el("span", { class: "sub", text: r.region }) : null));
        }
        cells.push(
          el("td", { class: "place-cell", "data-label": "City / town" },
            r.url ? el("a", { href: r.url, target: "_blank", rel: "noopener", text: placeName(r) }) : placeName(r)),
          el("td", { "data-label": "Levels suspended" }, el("span", { class: r.lifted ? "lifted" : "", text: levelsText(r.mask) }),
            r.notes.length ? el("span", { class: "sub", text: r.notes.join(" · ") }) : null),
          el("td", { class: "conf-cell", "data-label": "Confidence", title: r.why }, pips(r.conf), el("b", { text: ` ${r.conf}/5` }),
            el("span", { class: "sub", text: r.why })));
        body.push(el("tr", { class: i === 0 ? "group-start" : "" }, ...cells));
      });
    });
    $("#t-date").replaceChildren(head, el("tbody", {}, ...body));
  }

  // The live check's status line, then the date table (also redrawn when new results arrive)
  function renderBreakdownLive() {
    if (!LIVE) {
      $("#now-checked").textContent = "The live check couldn't be loaded; past dates are still shown.";
    } else {
      const age = Date.now() - new Date(LIVE.checked_at).getTime();
      $("#now-checked").textContent = `Coming days from the news, checked ${timeText(LIVE.checked_at)}. Checked every day at 5 AM; press Check now for the latest.`;
      const issues = [];
      if (age > 26 * 3600 * 1000) issues.push(`The checker hasn't run since ${timeText(LIVE.checked_at)}, so newer announcements may be missing.`);
      if ((LIVE.problems || []).some((p) => p.startsWith("AI couldn't read"))) issues.push("Some new articles haven't been read yet.");
      $("#now-banner").hidden = !issues.length;
      $("#now-banner").textContent = issues.join(" ");
    }
    renderByDate();
    if (state.town) renderTown();
  }

  // ── Check now: start a news check and wait for it ─────────────────────────
  const checkBtn = $("#check-now");
  const checkStatus = (text, busy) => {
    $("#check-status").hidden = !text;
    $("#check-status").textContent = text || "";
    checkBtn.disabled = Boolean(busy);
    checkBtn.textContent = busy ? "Checking…" : "Check now";
  };
  const callRefresh = (method) => fetch("api/refresh", { method, cache: "no-store" })
    .then((r) => r.json().catch(() => ({ state: "error", message: `The check service answered ${r.status}.` })));
  function waitForCheck() {
    checkStatus("Checking the news now. This takes about 3 to 5 minutes; you can keep reading.", true);
    const timer = setInterval(() => {
      callRefresh("GET").then((s) => {
        if (s.state === "running") return;
        clearInterval(timer);
        loadLive().then((l) => { LIVE = l; renderBreakdownLive(); }).catch(() => {});
        checkStatus(s.last_ok === false ? "The check didn't finish properly. The results shown are from the last good check."
          : "Done: showing the latest results.", false);
      }).catch(() => {});
    }, 20000);
  }
  checkBtn.addEventListener("click", () => {
    checkStatus("Starting a check…", true);
    callRefresh("POST").then((s) => {
      if (s.state === "started" || s.state === "running") waitForCheck();
      else checkStatus(s.message || "Couldn't start a check.", false);
    }).catch(() => checkStatus("Couldn't reach the check service. Try again in a minute.", false));
  });
  callRefresh("GET").then((s) => { if (s.state === "running") waitForCheck(); }).catch(() => {});

  // ── CSV ───────────────────────────────────────────────────────────────────
  function downloadCsv(name, rows) {
    if (!rows.length) return;
    const cols = Object.keys(rows[0]);
    const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const csv = [cols.join(","), ...rows.map((r) => cols.map((c) => q(r[c])).join(","))].join("\n");
    const a = el("a", { href: URL.createObjectURL(new Blob(["﻿" + csv], { type: "text/csv" })),
      download: `${name}${state.year === "all" ? "" : `_${state.year}`}${state.region ? `_${state.region.replace(/\W+/g, "_")}` : ""}.csv` });
    document.body.append(a);
    a.click();
    a.remove();
  }
})();
