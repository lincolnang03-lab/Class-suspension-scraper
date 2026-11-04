/* Class Suspension Tracker — figures computed in the browser from history.json (past), data.json (coming days)
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
  const state = { year: "all", searchYear: "all", region: "", date: "", town: null, townAll: false };
  let H = null, LIVE = null, RECS = [], YEARS = [], PLACES = [], SEARCH = [], uniqueNames = new Set();
  let HOLIDAYS = new Set(), BREAKS = [];

  // ── Load ──────────────────────────────────────────────────────────────────
  const getJSON = (url) => fetch(url, { cache: "no-store" }).then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); });
  const loadLive = () => {
    const urls = [window.LIVE_DATA_URL, "data.json"].filter(Boolean);
    const attempt = (i) => getJSON(`${urls[i]}?t=${Date.now()}`).catch((e) => (i + 1 < urls.length ? attempt(i + 1) : Promise.reject(e)));
    return attempt(0);
  };

  Promise.all([getJSON("history.json"), loadLive().catch(() => null), getJSON("places.json").catch(() => ({ places: [] })),
    getJSON("provinces.geojson").catch(() => null)])
    .then(([h, live, places, geo]) => {
      GEO = geo;
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
        ({ kind: isCity ? "City" : "Municipality", region, province, name, isCity: !!isCity, independent: !!independent,
           label: `${name}, ${province}, ${region}`, short: `${name}, ${province}` }));
      const provinces = new Map();
      PLACES.forEach((p) => { if (p.province && !provinces.has(p.province)) provinces.set(p.province, p.region); });
      SEARCH = [...[...provinces].map(([name, region]) => ({ kind: "Province", name, province: name, region, label: `${name}, ${region}`, short: name })),
        ...PLACES];
      const counts = countBy(PLACES, (p) => placeKey(p.name));
      uniqueNames = new Set([...counts].filter(([, n]) => n === 1).map(([k]) => k));
      const wantDate = params.get("date");
      if (/^\d{4}-\d{2}-\d{2}$/.test(wantDate || "")) state.date = wantDate;
      const wantTown = (params.get("town") || "").toLowerCase();  // full label, or the older "Town, Province" links
      state.town = SEARCH.find((p) => p.label.toLowerCase() === wantTown) || PLACES.find((p) => p.short.toLowerCase() === wantTown) || null;
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
  function periodRange(year = state.year) {
    if (year === "all") return [H.covered[0][0], lastDate()];
    const end = `${year}-12-31`;
    return [`${year}-01-01`, end < lastDate() ? end : lastDate()];
  }
  const schoolDayCache = new Map();
  function schoolDays(year = state.year) {
    const [a, b] = periodRange(year);
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
    bindSearch();
    $("#f-date").addEventListener("change", (e) => { state.date = e.target.value; renderByDate(); });
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
    renderMap(rs);
    renderFindings(rs);
    renderTown();
    renderBreakdownLive();
    $("#cal-count").textContent = `${fmt(schoolDays())} school days from ${period()}`;
    $("#footer").textContent = `Past data: ${fmt(RECS.length)} records on ${unique(RECS.map((r) => r.date)).length} days, to ${dayLabel(lastDate(), { month: "long", day: "numeric", year: "numeric" })}. Source: ${H.source}; coming days from the live news check.`;
  }

  // ── Overall: the map ───────────────────────────────────────────────────
  // Green (few) → yellow → red → dark red (most), by share of school days lost
  let GEO = null, MAP = null, mapReady = false;
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const STOPS = [[0, "#cfeed7"], [0.12, "#34c759"], [0.38, "#ffd60a"], [0.62, "#ff9f0a"], [0.84, "#ff3b30"], [1, "#8a0f08"]];
  const rgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  function heat(t) {
    const x = Math.max(0, Math.min(1, t));
    for (let i = 1; i < STOPS.length; i++) {
      const [t1, c1] = STOPS[i];
      if (x <= t1) {
        const [t0, c0] = STOPS[i - 1];
        const k = (x - t0) / (t1 - t0 || 1), a = rgb(c0), b = rgb(c1);
        return a.map((v, j) => Math.round(v + (b[j] - v) * k));
      }
    }
    return rgb(STOPS[STOPS.length - 1][1]);
  }
  const PH_BOUNDS = [[116.9, 4.5], [126.7, 21.2]];
  let lastMapRs = [];
  function homeView() {
    const box = $("#map");
    const vp = new deck.WebMercatorViewport({ width: box.clientWidth || 800, height: box.clientHeight || 560 });
    const fit = vp.fitBounds(PH_BOUNDS, { padding: 24 });
    return { longitude: fit.longitude, latitude: fit.latitude, zoom: fit.zoom, pitch: 0, bearing: 0, minZoom: fit.zoom - 0.5, maxZoom: 11 };
  }
  let flying = false;
  function flyTo(view) {
    currentView = view;          // buttons and resets animate the map; track where it's going, not where it is
    flying = !reduceMotion && view.transitionDuration !== 0;
    MAP.setProps({ initialViewState: { ...view, transitionDuration: reduceMotion ? 0 : (view.transitionDuration ?? 900),
      onTransitionEnd: () => { flying = false; },
      transitionInterpolator: new deck.LinearInterpolator(["longitude", "latitude", "zoom"]),
      transitionEasing: (t) => 1 - Math.pow(1 - t, 3) } });
  }
  let currentView = null;

  function initMap() {
    if (mapReady || !window.deck || !GEO) return mapReady;
    currentView = homeView();
    MAP = new deck.Deck({
      parent: $("#map"),
      views: new deck.MapView({ repeat: false }),
      initialViewState: currentView,
      // A flat map: pan and zoom only. Pinch, double-click or the buttons zoom; page scrolling is never captured
      controller: { scrollZoom: false, inertia: reduceMotion ? 0 : 350, dragRotate: false, touchRotate: false, keyboard: true },
      parameters: { clearColor: [0, 0, 0, 0] },
      onViewStateChange: ({ viewState }) => {
        if (flying) return;       // mid-animation frames report the old zoom; the target is already set
        currentView = viewState;
      },
      onHover: (info) => { showTip(info); hover(info.object || null); },
      // Click a province for its figures; click the sea to close them
      onClick: (info) => { $("#map-tip").hidden = true; pick(info.object || null); },
      getCursor: ({ isDragging, isHovering }) => (isDragging ? "grabbing" : isHovering ? "pointer" : "grab"),
    });
    const zoomBy = (d) => flyTo({ ...currentView, zoom: Math.max(currentView.minZoom ?? 4, Math.min(11, currentView.zoom + d)) });
    $("#map-in").addEventListener("click", () => zoomBy(0.6));
    $("#map-out").addEventListener("click", () => zoomBy(-0.6));
    $("#map-reset").addEventListener("click", () => flyTo(homeView()));
    $("#map-full").addEventListener("click", () => expandMap(!expanded));
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape" || e.target.id === "town") return;   // the search box handles its own Esc
      if (picked) pick(null);
      else if (expanded) expandMap(false);
    });
    // Re-fit when the card changes size (phone rotation, window resize)
    new ResizeObserver(() => { flyTo({ ...homeView(), transitionDuration: 0 }); renderMap(lastMapRs); }).observe($("#map"));
    mapReady = true;
    return true;
  }

  function showTip(info) {
    const tip = $("#map-tip");
    const f = info && info.object;
    if (!f) { tip.hidden = true; return; }
    const p = f.properties, st = p.stat, total = schoolDays();
    tip.replaceChildren(
      el("p", { class: "t" }, p.name, el("span", { text: ` · ${p.region}` })),
      st ? el("p", { class: "v" }, el("b", { text: pctText(st.days, total) }), ` of school days lost · ${st.days} of ${fmt(total)}`)
         : el("p", { class: "v", text: "No class suspensions recorded" }),
      st ? el("p", { class: "s", text: `Mostly ${st.cause.toLowerCase()} · ${plural(st.towns, "city or town", "cities and towns")} affected` }) : null);
    tip.hidden = false;
    const box = $("#map").getBoundingClientRect();
    const x = Math.min(info.x + 14, box.width - tip.offsetWidth - 8), y = Math.max(8, info.y - tip.offsetHeight - 12);
    tip.style.transform = `translate(${Math.max(8, x)}px, ${y}px)`;
  }

  // Hover and the picked province: a dark outline and a deeper shade (on those provinces and nothing else)
  let hovered = null, baseLayer = null, picked = null;
  const darker = (c, k = 0.82) => c.map((v, i) => (i < 3 ? Math.round(v * k) : v));
  function hover(f) {
    if ((f && f.properties.name) === (hovered && hovered.properties.name)) return;
    hovered = f;
    drawLayers();
  }
  function highlightLayers() {
    const data = baseLayer ? baseLayer.props.data : GEO.features;
    return unique([picked, hovered && hovered.properties.name].filter(Boolean)).map((name) =>
      new deck.GeoJsonLayer({ id: `outline-${name}`, data: data.filter((g) => g.properties.name === name), filled: true, stroked: true,
        getFillColor: (g) => darker(g.__fill || [200, 200, 200, 255]), getLineColor: [29, 29, 31, 255],
        lineWidthUnits: "pixels", getLineWidth: name === picked ? 3 : 2.5 }));
  }

  // ── Overall: a province's figures in a panel over the map (the same as searching it, made compact) ──
  let panelAll = false;
  function pick(f) {
    const name = f ? f.properties.name : null;
    if (name === picked) return;
    picked = name;
    panelAll = false;
    renderPanel();
    drawLayers();
  }
  function renderPanel() {
    const box = $("#map-panel");
    if (!picked) { box.hidden = true; box.replaceChildren(); return; }
    const shape = GEO.features.find((g) => g.properties.name === picked);
    const p = SEARCH.find((s) => s.kind === "Province" && placeKey(s.name) === placeKey(picked))
      || { kind: "Province", name: picked, province: picked, region: shape ? shape.properties.region : "" };
    const { hits, lost, total, causes } = summary(p, state.year);
    const byDate = datesOf(hits);
    const allDates = [...byDate.keys()].sort().reverse();
    const N = 5;
    const today = phToday(), live = liveFor(p), short = { month: "short", day: "numeric" };
    const close = el("button", { type: "button", class: "mp-close", "aria-label": `Close ${p.name}` });
    close.innerHTML = '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M6 6l8 8M14 6l-8 8" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>';
    close.addEventListener("click", () => pick(null));
    const more = allDates.length > N ? el("button", { type: "button", class: "btn small", text: panelAll ? "Show fewer" : `Show all ${allDates.length}` }) : null;
    if (more) more.addEventListener("click", () => { panelAll = !panelAll; renderPanel(); });
    box.setAttribute("aria-label", `${p.name}: class suspensions`);
    box.replaceChildren(
      el("div", { class: "mp-head" }, el("div", {}, el("h3", { text: p.name }), el("p", { text: `Province · ${p.region}` })), close),
      el("div", { class: "mp-stat" }, el("p", { class: "v", text: pctText(lost.length, total) }),
        el("p", { class: "s", text: `of school days lost: ${lost.length} of ${fmt(total)}, ${sinceText(state.year)}` })),
      el("div", { class: "mp-tiles" }, dayTile(p, live, today, "Today", short), dayTile(p, live, addDays(today, 1), "Tomorrow", short)),
      reasonsBlock(causes, lost),
      allDates.length
        ? el("div", {}, el("p", { class: "list-title", text: `${plural(allDates.length, "day")} with class suspensions` }),
          el("ul", { class: "list compact" }, ...(panelAll ? allDates : allDates.slice(0, N)).map((d) => el("li", {}, el("div", { class: "row" }, ...dayParts(p, byDate.get(d), d))))),
          more)
        : el("p", { class: "muted", text: "No class suspensions recorded in this period." }));
    box.hidden = false;
  }

  // ── Overall: the map, expanded to fill the screen (the page behind it stops scrolling) ──
  let expanded = false, keepSpace = null, scrollBack = 0;
  function expandMap(on) {
    if (on === expanded) return;
    expanded = on;
    const card = $(".map-card");
    if (on) {
      scrollBack = window.scrollY;
      keepSpace = el("div", { style: `height:${card.offsetHeight}px` });   // holds the card's place so the page doesn't jump
      card.before(keepSpace);
    } else if (keepSpace) {
      keepSpace.remove();
      keepSpace = null;
    }
    card.classList.toggle("expanded", on);
    document.documentElement.classList.toggle("map-locked", on);
    const btn = $("#map-full");
    btn.setAttribute("aria-pressed", String(on));
    btn.setAttribute("aria-label", on ? "Close the full-screen map" : "Expand the map to full screen");
    btn.innerHTML = on
      ? '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M8 3v5H3M12 3v5h5M8 17v-5H3M12 17v-5h5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>'
      : '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 8V3h5M17 8V3h-5M3 12v5h5M17 12v5h-5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    // Full screen: the scroll wheel zooms, since there's no page to scroll
    MAP.setProps({ controller: { scrollZoom: on, inertia: reduceMotion ? 0 : 350, dragRotate: false, touchRotate: false, keyboard: true } });
    $("#map-hint").textContent = on ? "Drag to move · scroll, pinch or +/− to zoom · click a province · Esc to close" : "Drag to move · pinch or +/− to zoom · click a province";
    if (!on) window.scrollTo(0, scrollBack);
  }
  function drawLayers() {
    if (MAP && baseLayer) MAP.setProps({ layers: [baseLayer, ...highlightLayers()] });
  }

  function renderMap(rs) {
    lastMapRs = rs;
    if (!initMap()) {
      if (!window.deck) $("#map").replaceChildren(el("p", { class: "map-fallback", text: "The map needs WebGL, which this browser doesn't have turned on." }));
      return;
    }
    const total = schoolDays();
    const stats = new Map(provinceRows(rs).map((p) => [placeKey(p.province), p]));
    const maxPct = Math.max(0.01, ...[...stats.values()].map((p) => p.pct));
    const key = `${state.year}|${state.region}`;
    const data = GEO.features.map((f) => ({ ...f, properties: { ...f.properties, stat: stats.get(placeKey(f.properties.name)) || null } }));
    const share = (f) => (f.properties.stat ? f.properties.stat.pct / maxPct : 0);
    const outside = (f) => state.region && f.properties.region !== state.region;
    const fill = (f) => (outside(f) ? [229, 229, 234, 255] : [...heat(f.properties.stat ? 0.08 + share(f) * 0.92 : 0), 255]);
    data.forEach((f) => { f.__fill = fill(f); });
    if (hovered) hovered = data.find((f) => f.properties.name === hovered.properties.name && f.geometry === hovered.geometry) || null;
    baseLayer = new deck.GeoJsonLayer({
        id: "provinces", data, filled: true, stroked: true, pickable: true,
        getFillColor: (f) => f.__fill,
        // thin white borders keep neighbouring provinces apart
        getLineColor: [255, 255, 255, 230], lineWidthUnits: "pixels", getLineWidth: 0.8, lineWidthMinPixels: 0.5,
        transitions: reduceMotion ? {} : { getFillColor: { duration: 400 } },
        updateTriggers: { getFillColor: key },
      });
    drawLayers();
    // Legend: the same colours, labelled with real percentages
    const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => `${Math.round(t * maxPct)}%`);
    $("#map-legend").replaceChildren(
      el("span", { class: "lg-none" }, el("i", { style: `background:${STOPS[0][1]}` }), "None"),
      el("div", { class: "lg-scale" },
        el("div", { class: "lg-bar", style: `background:linear-gradient(90deg, ${STOPS.slice(1).map(([t, c]) => `${c} ${Math.round(((t - 0.12) / 0.88) * 100)}%`).join(", ")})` }),
        el("div", { class: "lg-ticks" }, ...ticks.map((t) => el("span", { text: t })))),
      el("span", { class: "lg-note", text: `of ${fmt(total)} school days lost${where()}` }));
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
    const total = schoolDays();
    const provs = provinceRows(rs).sort((a, b) => b.days - a.days);
    const kpi = (v, l, s) => el("div", { class: "kpi" }, el("p", { class: "v", text: v }), el("p", { class: "l", text: l }), el("p", { class: "s", text: s }));
    $("#kpis").replaceChildren(
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
    out.push(el("li", {}, "From ", period(), where(), ", cities, towns and provinces suspended classes ", b(`${fmt(lgu.length)} times`),
      ` (one LGU for one day), across ${plural(lost.length, "school day")}.`,
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

  // ── Cause names and colours (used by the search result) ───────────────────
  const SHORT_CAUSE = { "Typhoon / tropical storm": "Typhoon or storm", "Low pressure area / shear line": "Low pressure" };
  const groupOf = (cause) => GROUPS.find(([, causes]) => causes.includes(cause)) || GROUPS[3];

  // ── Breakdown: search a city, municipality or province ────────────────────
  // A record applies to a town if it names it or covers it (province-wide, except independent cities
  // outside Metro Manila; region-wide; nationwide). It applies to a province if any of its LGUs
  // suspended, or a region-wide or nationwide order covered it.
  function covers(r, p) {
    const prov = placeKey(r.province), city = placeKey(r.city === "all cities" ? "" : r.city);
    if (p.kind === "Province") return prov === placeKey(p.name) || (r.scope === "region" && r.region === p.region) || r.scope === "national";
    if (r.scope === "city") return city === placeKey(p.name) && (prov === placeKey(p.province) || !r.province || uniqueNames.has(placeKey(p.name)));
    if (r.scope === "province") {
      if (p.isCity && prov === placeKey(p.name)) return true;
      return prov === placeKey(p.province) && (!p.independent || p.province === "Metro Manila");
    }
    if (r.scope === "region") return r.region === p.region;
    return r.scope === "national";
  }
  // Totals for one place: for the year picked next to the search box, or the map's year
  function summary(p, y = state.searchYear) {
    const [a, b] = periodRange(y);
    const hits = RECS.filter((r) => r.date >= a && r.date <= b && covers(r, p));
    const lost = lostDays(hits);
    return { hits, lost, total: schoolDays(y), causes: [...countBy(lost, (d) => H.days[d].cause)].sort((x, y) => y[1] - x[1]) };
  }
  const sinceText = (y) => (y === "all" ? `since Jan ${YEARS[0]}` : yearLabel(y));

  // ── A place's figures, shared by the search result and the map's province panel ──
  const liveFor = (p) => (LIVE ? (LIVE.records || []).filter((r) => SHOWN.has(r.status) && covers(r, p)) : []);
  function dayTile(p, live, d, word, when = { weekday: "short", month: "short", day: "numeric" }) {
    const rs = live.filter((r) => r.date === d);
    const act = rs.filter((r) => ACTIVE.has(r.status));
    let text = "No suspension announced";
    if (act.length && p.kind !== "Province") text = `${act[0].status === "online_or_modular" ? "Moved online" : "Suspended"}: ${levelsText(maskOf(act[0].levels))}`;
    else if (act.length) text = act.some((r) => r.scope !== "city") ? "Suspended province-wide" : `${plural(act.length, "place")} suspended`;
    else if (rs.some((r) => r.status === "lifted")) text = "Suspension lifted";
    return el("div", { class: `tile${act.length ? " on" : ""}` }, el("p", { class: "k", text: `${word} · ${dayLabel(d, when)}` }),
      el("p", { class: "v" }, el("span", { class: "dot", "aria-hidden": "true" }), text));
  }
  // Why school days were lost: days per cause, as bars
  function reasonsBlock(causes, lost) {
    if (!causes.length) return null;
    const top = causes[0][1];
    return el("div", { class: "reasons" },
      el("p", { class: "list-title", text: "Why school days were lost" }),
      el("ul", { class: "reason-list" }, ...causes.map(([c, n]) => el("li", {},
        el("span", { class: "rn" }, el("i", { style: `background:${css(groupOf(c)[2])}` }), SHORT_CAUSE[c] || c),
        el("span", { class: "rb" }, el("i", { style: `width:${Math.max(4, Math.round((100 * n) / top))}%; background:${css(groupOf(c)[2])}` })),
        el("span", { class: "rv", text: `${plural(n, "day")} · ${pctText(n, lost.length)}` })))));
  }
  function datesOf(hits) {
    const byDate = new Map();
    hits.forEach((r) => { if (!byDate.has(r.date)) byDate.set(r.date, []); byDate.get(r.date).push(r); });
    return byDate;
  }
  // One day's line. A town: its own announcement wins over a province-wide one. A province: count its LGUs.
  function dayParts(p, rs, d) {
    const isProv = p.kind === "Province";
    const cause = H.days[d].cause;
    const wide = rs.find((r) => r.scope !== "city" && r.scope !== "partial");
    const own = rs.find((r) => r.scope === "city");
    const rep = isProv ? (wide || rs[0]) : (own || wide || rs[0]);
    const towns = unique(rs.filter((r) => r.scope === "city").map((r) => placeKey(r.city))).length;
    const main = isProv ? (wide ? `Whole province · ${levelsText(rep.mask)}` : plural(towns, "city or town", "cities and towns")) : levelsText(rep.mask);
    const details = [!isProv && !own && wide ? "province-wide" : "", isProv && wide && towns ? `plus ${plural(towns, "local order")}` : "",
      rep.inPerson ? "face-to-face only" : "", isSchoolDay(d) ? "" : "not a school day"].filter(Boolean);
    return [
      el("span", { class: "d", text: dayLabel(d, { weekday: "short", month: "short", day: "numeric" }) }),
      el("span", { class: "what" }, main, details.length ? el("small", { text: details.join(" · ") }) : null),
      el("span", { class: "tag", style: `--c: ${css(groupOf(cause)[2])}`, text: SHORT_CAUSE[cause] || cause }),
    ];
  }

  // Search: a combobox with a custom results list (keyboard: ↑ ↓ Enter Esc)
  const norm = (s) => s.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
  let matches = [], active = -1;
  function bindSearch() {
    const input = $("#town"), list = $("#town-options");
    const drawYear = () => seg($("#f-search-year"), [["all", "All time"], ...YEARS.map((y) => [y, yearLabel(y)])], state.searchYear, (v) => {
      state.searchYear = v; state.townAll = false; drawYear();
      renderTown();
      if (!list.hidden) input.dispatchEvent(new Event("input"));   // refresh the totals in an open list
    });
    drawYear();
    const open = (on) => { list.hidden = !on; input.setAttribute("aria-expanded", String(on)); if (!on) input.removeAttribute("aria-activedescendant"); };
    const choose = (p) => { state.town = p; state.townAll = false; input.value = p.label; open(false); $(".bydate").classList.remove("show-day"); renderTown(); };
    const paint = () => {
      const q = norm(input.value.trim());
      list.replaceChildren(...(matches.length ? matches.map((p, i) => {
        const nm = norm(p.name), at = nm.indexOf(q);
        const name = at < 0 ? [p.name] : [p.name.slice(0, at), el("mark", { text: p.name.slice(at, at + q.length) }), p.name.slice(at + q.length)];
        const sum = summary(p);
        const where = p.kind === "Province" ? `, ${p.region}` : `, ${p.province}, ${p.region}`;
        const li = el("li", { role: "option", id: `town-opt-${i}`, "aria-selected": String(i === active) },
          el("span", { class: "line" }, el("span", { class: "nm" }, ...name), el("span", { class: "pl", text: where })),
          el("span", { class: "meta", text: `${p.kind} · ${sum.lost.length ? `${plural(sum.lost.length, "school day")} lost (${pctText(sum.lost.length, sum.total)})` : "no school days lost"}` }));
        li.addEventListener("pointerdown", (e) => { e.preventDefault(); choose(p); });  // respond on press
        return li;
      }) : [el("li", { class: "none", role: "option", "aria-disabled": "true", text: "No city, municipality or province by that name" })]));
      if (active >= 0) { input.setAttribute("aria-activedescendant", `town-opt-${active}`); $(`#town-opt-${active}`)?.scrollIntoView({ block: "nearest" }); }
    };
    input.addEventListener("input", () => {
      const q = norm(input.value.trim());
      $("#town-clear").hidden = !input.value;
      if (q.length < 2) { matches = []; open(false); return; }
      // Exact name first, then names that start with the text, then names that contain it; provinces before towns
      const exact = [], starts = [], has = [];
      SEARCH.forEach((p) => { const n = norm(p.name); if (n === q) exact.push(p); else if (n.startsWith(q)) starts.push(p); else if (n.includes(q)) has.push(p); });
      matches = [...exact, ...starts, ...has].slice(0, 8);
      active = matches.length ? 0 : -1;
      paint();
      open(true);
    });
    input.addEventListener("keydown", (e) => {
      if (list.hidden || !matches.length) { if (e.key === "Escape") open(false); return; }
      if (e.key === "ArrowDown") { e.preventDefault(); active = (active + 1) % matches.length; paint(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); active = (active - 1 + matches.length) % matches.length; paint(); }
      else if (e.key === "Enter") { e.preventDefault(); if (active >= 0) choose(matches[active]); }
      else if (e.key === "Escape") open(false);
    });
    input.addEventListener("blur", () => open(false));
    $("#town-clear").addEventListener("click", () => { input.value = ""; state.town = null; $("#town-clear").hidden = true; renderTown(); input.focus(); });
    if (state.town) { input.value = state.town.label; $("#town-clear").hidden = false; }
  }

  // The result: today, tomorrow, school days lost, why they were lost, then every suspension by month
  const chevron = () => { const s = document.createElementNS("http://www.w3.org/2000/svg", "svg"); s.setAttribute("class", "chev"); s.setAttribute("viewBox", "0 0 8 13"); s.setAttribute("aria-hidden", "true"); s.innerHTML = '<path d="M1.5 1.5l5 5-5 5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'; return s; };
  function renderTown() {
    const p = state.town;
    try { const u = new URL(location.href); p ? u.searchParams.set("town", p.label) : u.searchParams.delete("town"); history.replaceState(null, "", u); } catch { /* sandboxed */ }
    // While a place is picked, the card is about that place: the country-wide day totals step aside
    const card = $(".bydate");
    card.classList.toggle("searching", Boolean(p));
    if (!p) { $("#town-result").replaceChildren(); state.townAll = false; card.classList.remove("show-day"); return; }
    const isProv = p.kind === "Province";
    const { hits, lost, total, causes } = summary(p);
    const byDate = datesOf(hits);
    const allDates = [...byDate.keys()].sort().reverse();
    const SHOWN_N = 8;
    const dates = state.townAll ? allDates : allDates.slice(0, SHOWN_N);
    const today = phToday();
    const live = liveFor(p);
    const months = new Map();
    dates.forEach((d) => { const m = d.slice(0, 7); if (!months.has(m)) months.set(m, []); months.get(m).push(d); });
    const row = (d) => {
      const btn = el("button", { type: "button", class: "row", "aria-label": `${dayLabel(d, { weekday: "long", month: "long", day: "numeric", year: "numeric" })}: open every place suspended that day` },
        ...dayParts(p, byDate.get(d), d), chevron());
      btn.addEventListener("click", () => {
        state.date = d; renderByDate(); card.classList.add("show-day");
        $(".date-row").scrollIntoView({ behavior: "smooth", block: "start" });
      });
      return el("li", {}, btn);
    };
    const sub = isProv ? `Province · ${p.region}` : `${p.kind} · ${p.province}, ${p.region}${p.independent && p.province !== "Metro Manila" ? " · independent city" : ""}`;
    $("#town-result").replaceChildren(el("div", { class: "town" },
      el("div", { class: "town-head" }, el("h3", { text: p.name }), el("p", { text: sub })),
      el("div", { class: "town-tiles" },
        dayTile(p, live, today, "Today"), dayTile(p, live, addDays(today, 1), "Tomorrow"),
        el("div", { class: "tile" }, el("p", { class: "k", text: "School days lost" }), el("p", { class: "v big", text: pctText(lost.length, total) }),
          el("p", { class: "s", text: `${lost.length} of ${fmt(total)} school days, ${sinceText(state.searchYear)}` }))),
      reasonsBlock(causes, lost),
      dates.length
        ? el("div", {}, el("p", { class: "list-title", text: `${plural(allDates.length, "day", "days")} with class suspensions${allDates.length > dates.length ? ` · latest ${dates.length}` : ""}` }),
          ...[...months].flatMap(([m, ds]) => [el("p", { class: "group-label", text: `${MONTH_FULL[+m.slice(5) - 1]} ${m.slice(0, 4)}` }), el("ul", { class: "list" }, ...ds.map(row))]),
          allDates.length > SHOWN_N ? (() => {
            const more = el("button", { type: "button", class: "btn more-btn", text: state.townAll ? "Show fewer" : `Show all ${allDates.length}` });
            more.addEventListener("click", () => { state.townAll = !state.townAll; renderTown(); });
            return more;
          })() : null)
        : el("p", { class: "muted", text: `No class suspensions recorded for ${p.name} in this period.` })));
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
    if (picked) renderPanel();
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

  // ── Cost tracker: what the Claude API has cost, by day and all time (costs.json, from costs.py) ──
  const MODEL_NAME = { "claude-haiku-5-5": "Haiku 5.5", "claude-sonnet-5-5": "Sonnet 5.5", "claude-opus-5-5": "Opus 5.5" };
  const JOB_NAME = { "live scheduled": "Daily check", "live manual": "Check now", "live local": "Test check", live: "News check",
    trial: "Trial run", accuracy: "Accuracy check" };
  const money = (n) => (n === 0 ? "$0.00" : n < 0.01 ? "under $0.01" : `$${n.toFixed(2)}`);
  // One day's jobs in words: "Daily check ($0.03), Check now ($0.01)"; trial runs name the months of news they tested
  function jobsText(byJob) {
    const groups = new Map();
    Object.entries(byJob).forEach(([j, v]) => {
      const [kind, ym] = j.split(" ");
      const name = JOB_NAME[j] || JOB_NAME[kind] || kind;
      if (!groups.has(name)) groups.set(name, { usd: 0, months: [] });
      const g = groups.get(name);
      g.usd += v;
      if (/^\d{4}-\d{2}$/.test(ym || "")) g.months.push(ym);
    });
    const list = [...groups].sort((a, b) => b[1].usd - a[1].usd);
    return list.map(([name, g]) => {
      const ms = g.months.sort();
      const years = unique(ms.map((m) => m.slice(0, 4)));
      const on = ms.length ? ` on ${years.map((y) => `${ms.filter((m) => m.startsWith(y)).map((m) => MON[+m.slice(5) - 1]).join(", ")} ${y}`).join("; ")} news` : "";
      return `${name}${on}${list.length > 1 ? ` (${money(g.usd)})` : ""}`;
    }).join(", ");
  }
  let costAll = false;
  function renderCosts(c) {
    if (!c) { $("#cost-sub").textContent = "The cost summary couldn't be loaded."; return; }
    const today = phToday();
    const days = c.days || [];
    const since = addDays(today, -29);
    const last30 = days.filter((d) => d.date >= since);
    const todayRow = days.find((d) => d.date === today);
    const kpi = (v, l, s) => el("div", { class: "kpi" }, el("p", { class: "v", text: v }), el("p", { class: "l", text: l }), el("p", { class: "s", text: s }));
    $("#cost-sub").textContent = days.length
      ? `Every Claude call, priced by its tokens. Last API use: ${dayLabel(days[0].date, { month: "short", day: "numeric", year: "numeric" })}. Days with no API use aren't listed.`
      : "No API calls recorded yet.";
    $("#cost-kpis").replaceChildren(
      kpi(money(c.total_usd), "All time", `${plural(c.calls, "API call")} on ${plural(days.length, "day")}`),
      kpi(money(todayRow ? todayRow.usd : 0), "Today", `${dayLabel(today, { weekday: "short", month: "short", day: "numeric" })}, Philippine time${todayRow ? ` · ${plural(todayRow.calls, "call")}` : ""}`),
      kpi(money(last30.reduce((s, d) => s + d.usd, 0)), "Last 30 days", plural(last30.length, "day with API use", "days with API use")));
    const head = el("thead", {}, el("tr", {}, ...["Date", "What ran", "Calls", "Tokens in / out", "Cost"].map((t, i) =>
      el("th", { scope: "col", class: i >= 2 ? "num" : "", text: t }))));
    const N = 14;
    const rows = (costAll ? days : days.slice(0, N)).map((d) => el("tr", {},
      el("td", { class: "cost-date" }, dayLabel(d.date, { weekday: "short", month: "short", day: "numeric", year: "numeric" }),
        d.estimated ? el("span", { class: "sub", text: "estimated from saved token counts" }) : null),
      el("td", {}, jobsText(d.by_job),
        el("span", { class: "sub", text: Object.keys(d.by_model).map((m) => MODEL_NAME[m] || m).join(", ") })),
      el("td", { class: "num c-calls", text: fmt(d.calls) }),
      el("td", { class: "num c-tok", text: `${fmt(d.input_tokens)} / ${fmt(d.output_tokens)}` }),
      el("td", { class: "num cost-usd", text: money(d.usd) })));
    const foot = el("tfoot", {}, el("tr", {},
      el("th", { scope: "row", colspan: "2", text: "All time" }),
      el("td", { class: "num c-calls", text: fmt(c.calls) }),
      el("td", { class: "num c-tok", text: `${fmt(days.reduce((s, d) => s + d.input_tokens, 0))} / ${fmt(days.reduce((s, d) => s + d.output_tokens, 0))}` }),
      el("td", { class: "num cost-usd", text: money(c.total_usd) })));
    $("#t-cost").replaceChildren(head, el("tbody", {}, ...(rows.length ? rows
      : [el("tr", {}, el("td", { colspan: "5", class: "empty-cell", text: "Nothing spent yet." }))])), foot);
    const prices = Object.entries(c.prices || {}).map(([m, p]) => `${MODEL_NAME[m] || m} $${p.input.toFixed(2)} / $${p.output.toFixed(2)}`).join(" · ");
    const more = days.length > N ? el("button", { type: "button", class: "btn small", text: costAll ? "Show fewer days" : `Show all ${days.length} days` }) : null;
    if (more) more.addEventListener("click", () => { costAll = !costAll; renderCosts(c); });
    $("#cost-note").replaceChildren(more || "", more ? el("br") : "",
      `Prices per million tokens, in / out: ${prices}. Rappler is read without AI, so it costs nothing. Updated after every check.`);
  }
  // Hidden until someone opens it; the figures load on the first open
  let costsLoaded = false;
  $("#cost-tracker").addEventListener("click", (e) => {
    const open = e.currentTarget.getAttribute("aria-expanded") !== "true";
    e.currentTarget.setAttribute("aria-expanded", String(open));
    $("#cost-panel").hidden = !open;
    if (!open) return;
    if (!costsLoaded) {
      costsLoaded = true;
      getJSON(`costs.json?t=${Date.now()}`).then(renderCosts).catch(() => renderCosts(null));
    }
    $("#cost-panel").scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "nearest" });
  });

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
