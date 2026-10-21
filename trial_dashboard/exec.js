/* Lost School Days — figures computed in the browser from history.json (past) and data.json (upcoming). */
(() => {
  const REGION_ORDER = [
    "NCR", "CAR", "Ilocos Region", "Cagayan Valley", "Central Luzon", "CALABARZON", "MIMAROPA",
    "Bicol Region", "Western Visayas", "Negros Island Region", "Central Visayas", "Eastern Visayas",
    "Zamboanga Peninsula", "Northern Mindanao", "Davao Region", "SOCCSKSARGEN", "Caraga", "BARMM",
  ];
  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const MONTH_FULL = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  const LEVEL_LABELS = ["Preschool", "Elementary", "Junior High", "Senior High", "College"];
  const ACTIVE = new Set(["suspended", "online_or_modular", "opening_postponed"]);
  const PROV_SHOWN = 15;

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
  const pct = (a, b) => (b ? Math.round((100 * a) / b) : 0);
  const plural = (n, w, ws = `${w}s`) => `${fmt(n)} ${n === 1 ? w : ws}`;
  const regionRank = (r) => REGION_ORDER.indexOf(r) + 1 || 99;
  const phToday = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Manila" }).format(new Date());
  const addDays = (iso, n) => { const d = new Date(`${iso}T00:00:00Z`); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
  const dayLabel = (iso, opts) => new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", { timeZone: "UTC", ...opts });
  const timeText = (iso) => new Date(iso).toLocaleString("en-US", { timeZone: "Asia/Manila", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  const unique = (xs) => [...new Set(xs)];
  const countBy = (xs, f) => xs.reduce((m, x) => m.set(f(x), (m.get(f(x)) || 0) + 1), new Map());

  const state = { year: "all", region: "", metric: "lgu", provSort: "days", provDir: -1, provAll: false };
  let H = null, LIVE = null, RECS = [], CAUSES = [], YEARS = [], FULL_YEARS = [];
  const charts = {};

  // ── Load ──────────────────────────────────────────────────────────────────
  const getJSON = (url) => fetch(url, { cache: "no-store" }).then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); });
  const loadLive = () => {
    const urls = [window.LIVE_DATA_URL, "data.json"].filter(Boolean);
    const attempt = (i) => getJSON(`${urls[i]}?t=${Date.now()}`).catch((e) => (i + 1 < urls.length ? attempt(i + 1) : Promise.reject(e)));
    return attempt(0);
  };

  Promise.all([getJSON("history.json"), loadLive().catch(() => null)]).then(([h, live]) => {
    H = h;
    LIVE = live;
    CAUSES = h.causes;
    RECS = h.records.map(([date, region, province, city, scope, mask, sector, inPerson, partDay]) => ({
      date, region, province, city, scope, mask, sector, inPerson: !!inPerson, partDay: !!partDay,
      year: date.slice(0, 4), month: date.slice(0, 7), cause: h.days[date].cause,
    }));
    YEARS = unique(RECS.map((r) => r.year)).sort();
    // A year counts as complete for averages if the data covers January to December
    FULL_YEARS = h.covered.filter(([a, b]) => a.endsWith("-01-01") && b.endsWith("-12-31")).map(([a]) => a.slice(0, 4));
    buildControls();
    render();
    renderNow();
    setInterval(() => loadLive().then((l) => { LIVE = l; renderNow(); }).catch(() => {}), 5 * 60 * 1000);
  }).catch((e) => { $("#lede").textContent = `Couldn't load the data (${e.message}).`; });

  // ── Filters ───────────────────────────────────────────────────────────────
  function lastDate() { return H.covered[H.covered.length - 1][1]; }
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

  function buildControls() {
    const drawYear = () => seg($("#f-year"), [["all", `All (${YEARS[0]}–${YEARS[YEARS.length - 1]})`], ...YEARS.map((y) => [y, yearLabel(y)])],
      state.year, (v) => { state.year = v; drawYear(); render(); });
    drawYear();
    const regions = unique(RECS.map((r) => r.region).filter(Boolean)).sort((a, b) => regionRank(a) - regionRank(b));
    $("#f-region").replaceChildren(el("option", { value: "", text: "All regions" }), ...regions.map((r) => el("option", { value: r, text: r })));
    $("#f-region").addEventListener("change", (e) => { state.region = e.target.value; render(); });
    const drawMetric = () => seg($("#f-metric"), [["lgu", "LGU suspensions"], ["days", "School days"]], state.metric,
      (v) => { state.metric = v; drawMetric(); renderTimeline(filtered()); });
    drawMetric();
    $("#dl-prov").addEventListener("click", () => downloadCsv("provinces", provinceRows(filtered()).map((p) => ({
      province: p.province, region: p.region, school_days_with_suspensions: p.days, province_wide_days: p.wideDays,
      cities_and_towns_affected: p.towns, lgu_suspensions: p.lgu, main_cause: p.cause }))));
    $("#dl-all").addEventListener("click", () => downloadCsv("class_suspensions", filtered().map((r) => ({
      date: r.date, region: r.region, province: r.province, city_or_town: r.city || "(whole province)", scope: r.scope,
      levels: r.mask < 0 ? "not stated" : LEVEL_LABELS.filter((_, i) => r.mask & (1 << i)).join("; "),
      public_private: r.sector || "not stated", face_to_face_only: r.inPerson ? "yes" : "no", part_of_day: r.partDay ? "yes" : "no",
      cause: r.cause, storm: H.days[r.date].storms.join("; "), source: H.days[r.date].url }))));
    $("#prov-more").addEventListener("click", () => { state.provAll = !state.provAll; renderProvinces(filtered()); });
  }

  const inRegion = (r) => !state.region || r.region === state.region;
  const filtered = () => RECS.filter((r) => (state.year === "all" || r.year === state.year) && inRegion(r));
  const lguOnly = (rs) => rs.filter((r) => r.scope !== "partial");
  const where = () => (state.region ? ` in ${state.region}` : "");
  const period = () => (state.year === "all" ? `${dayLabel(H.covered[0][0], { month: "short", year: "numeric" })} to ${dayLabel(lastDate(), { month: "short", day: "numeric", year: "numeric" })}` : yearLabel(state.year));

  function render() {
    const rs = filtered();
    renderKpis(rs);
    renderFindings(rs);
    renderTimeline(rs);
    renderCauses(rs);
    renderSeason();
    renderProvinces(rs);
    renderEvents(rs);
    renderLevels(rs);
    $("#coverage").textContent = `Past data covers ${H.covered.map(([a, b]) => `${dayLabel(a, { month: "short", day: "numeric", year: "numeric" })} – ${dayLabel(b, { month: "short", day: "numeric", year: "numeric" })}`).join("; ")}.`;
    $("#footer").textContent = `Built from ${fmt(RECS.length)} records on ${unique(RECS.map((r) => r.date)).length} days. Source: ${H.source}; upcoming suspensions from the live news checker.`;
  }

  // ── Key figures ───────────────────────────────────────────────────────────
  function provinceRows(rs) {
    const by = new Map();
    rs.filter((r) => r.province).forEach((r) => {
      if (!by.has(r.province)) by.set(r.province, []);
      by.get(r.province).push(r);
    });
    return [...by].map(([province, list]) => {
      const lgu = lguOnly(list);
      const days = unique(list.map((r) => r.date));
      const causes = countBy(days, (d) => H.days[d].cause);
      return {
        province, region: list[0].region, days: days.length,
        wideDays: unique(list.filter((r) => r.scope === "province").map((r) => r.date)).length,
        towns: unique(lgu.filter((r) => r.city).map((r) => r.city.toLowerCase())).length,
        lgu: lgu.length,
        cause: [...causes].sort((a, b) => b[1] - a[1])[0][0],
      };
    });
  }

  function renderKpis(rs) {
    const lgu = lguOnly(rs);
    const days = unique(rs.map((r) => r.date));
    const provs = provinceRows(rs).sort((a, b) => b.days - a.days);
    const typhoonDays = days.filter((d) => H.days[d].cause === "Typhoon / tropical storm").length;
    const kpi = (v, l, s) => el("div", { class: "kpi" }, el("p", { class: "v", text: v }), el("p", { class: "l", text: l }), el("p", { class: "s", text: s }));
    $("#kpis").replaceChildren(
      kpi(fmt(days.length), "School days with suspensions", `days when at least one LGU${where()} suspended classes`),
      kpi(fmt(lgu.length), "LGU suspensions", "one city, town or province suspending classes for one day"),
      kpi(fmt(provs.length), "Provinces affected", state.region ? `in ${state.region}` : "including Metro Manila"),
      provs.length ? kpi(`${provs[0].days} days`, `Hardest hit: ${provs[0].province}`, `school days with a suspension somewhere in the province`) : kpi("—", "Hardest hit", ""),
      kpi(`${pct(typhoonDays, days.length)}%`, "Of suspension days caused by typhoons", `${typhoonDays} of ${days.length} days`),
    );
  }

  // ── Findings, written from the figures ────────────────────────────────────
  function renderFindings(rs) {
    const out = [];
    const lgu = lguOnly(rs);
    const days = unique(rs.map((r) => r.date));
    if (!days.length) {
      $("#findings").replaceChildren(el("li", { text: `No suspensions recorded${where()} in ${period()}.` }));
      return;
    }
    const b = (t) => el("b", { text: t });
    out.push(el("li", {}, "From ", period(), where(), ", classes were suspended on ", b(plural(days.length, "school day")),
      ", adding up to ", b(fmt(lgu.length)), " LGU suspensions (one city, town or province for one day).",
      state.year === "all" ? ` By year: ${YEARS.map((y) => `${yearLabel(y)} — ${fmt(lguOnly(rs.filter((r) => r.year === y)).length)}`).join(", ")}.` : ""));

    const byCause = [...countBy(days, (d) => H.days[d].cause)].sort((a, c) => c[1] - a[1]);
    const heatDays = days.filter((d) => H.days[d].cause === "Extreme heat").sort();
    out.push(el("li", {}, b(byCause[0][0]), ` was the leading cause, on ${byCause[0][1]} of ${days.length} days (${pct(byCause[0][1], days.length)}%)`,
      byCause[1] ? `, followed by ${byCause[1][0].toLowerCase()} (${byCause[1][1]} days)` : "", ".",
      heatDays.length ? el("span", {}, " ", b(`Extreme heat`), ` suspended classes on ${plural(heatDays.length, "day")}, all in ${unique(heatDays.map((d) => `${MONTH_FULL[+d.slice(5, 7) - 1]} ${d.slice(0, 4)}`)).join(", ")}: a cause tied to when the school year runs, not only to the weather.`) : ""));

    const provs = provinceRows(rs).sort((a, c) => c.days - a.days).slice(0, 3);
    out.push(el("li", {}, "Most affected: ", ...provs.flatMap((p, i) => [i ? (i === provs.length - 1 ? ", and " : ", ") : "", b(p.province), ` (${p.days} days)`]),
      ". Their counts include days when only some towns suspended."));

    const peak = [...countBy(lgu, (r) => +r.date.slice(5, 7))].sort((a, c) => c[1] - a[1]);
    const jn = lgu.filter((r) => { const m = +r.date.slice(5, 7); return m >= 7 && m <= 11; }).length;
    out.push(el("li", {}, b(`${pct(jn, lgu.length)}%`), " of LGU suspensions fell between July and November",
      peak.length ? `; the single busiest month was ${MONTH_FULL[peak[0][0] - 1]}` : "", ". That window overlaps the first half of the school year."));

    const worst = [...countBy(lgu, (r) => r.month)].sort((a, c) => c[1] - a[1])[0];
    if (worst && unique(lgu.map((r) => r.month)).length > 1) {
      const wr = rs.filter((r) => r.month === worst[0]);
      const wc = [...countBy(unique(wr.map((r) => r.date)), (d) => H.days[d].cause)].sort((a, c) => c[1] - a[1])[0][0];
      out.push(el("li", {}, "The worst single month was ", b(`${MONTH_FULL[+worst[0].slice(5) - 1]} ${worst[0].slice(0, 4)}`),
        `: ${fmt(worst[1])} LGU suspensions on ${plural(unique(wr.map((r) => r.date)).length, "school day")}${where()}, mostly from ${wc.toLowerCase()}.`));
    }

    const f2f = lgu.filter((r) => r.inPerson).length;
    const allLv = lgu.filter((r) => r.mask === 31).length;
    out.push(el("li", {}, b(`${pct(f2f, lgu.length)}%`), " of suspensions were for face-to-face classes only (learning moved to modular or online); ",
      b(`${pct(allLv, lgu.length)}%`), " suspended every level from preschool to college."));

    out.push(el("li", { class: "muted" }, "These counts are a lower bound: Rappler's roundups miss many single-town suspensions (see How these numbers are made)."));
    $("#findings").replaceChildren(...out);
  }

  // ── Charts ────────────────────────────────────────────────────────────────
  const causeColor = (c) => css(`--c${CAUSES.indexOf(c) + 1}`) || css("--c9");
  function chartDefaults() {
    Chart.defaults.font.family = css("--font-body");
    Chart.defaults.color = css("--muted");
    Chart.defaults.borderColor = css("--line");
  }
  function draw(id, config) {
    chartDefaults();
    if (charts[id]) charts[id].destroy();
    charts[id] = new Chart(document.getElementById(id), config);
  }

  function monthsInView() {
    const [start] = H.covered[0];
    const end = lastDate();
    const from = state.year === "all" ? start.slice(0, 7) : `${state.year}-01`;
    const to = state.year === "all" ? end.slice(0, 7) : (state.year === end.slice(0, 4) ? end.slice(0, 7) : `${state.year}-12`);
    const out = [];
    let [y, m] = from.split("-").map(Number);
    while (`${y}-${String(m).padStart(2, "0")}` <= to) {
      out.push(`${y}-${String(m).padStart(2, "0")}`);
      m += 1;
      if (m > 12) { m = 1; y += 1; }
    }
    return out;
  }

  function renderTimeline(rs) {
    const months = monthsInView();
    const lgu = lguOnly(rs);
    const used = CAUSES.filter((c) => rs.some((r) => r.cause === c));
    const value = (month, cause) => (state.metric === "lgu"
      ? lgu.filter((r) => r.month === month && r.cause === cause).length
      : unique(rs.filter((r) => r.month === month && r.cause === cause).map((r) => r.date)).length);
    $("#timeline-sub").textContent = state.metric === "lgu"
      ? `LGU suspensions per month${where()}, coloured by cause.`
      : `School days per month with at least one suspension${where()}, coloured by cause.`;
    draw("c-timeline", {
      type: "bar",
      data: {
        labels: months.map((m) => (state.year === "all" ? `${MON[+m.slice(5) - 1]} ${m.slice(2, 4)}` : MON[+m.slice(5) - 1])),
        datasets: used.map((c) => ({ label: c, data: months.map((m) => value(m, c)), backgroundColor: causeColor(c), borderWidth: 0, borderRadius: 2 })),
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { position: "bottom", labels: { boxWidth: 10, boxHeight: 10 } }, tooltip: { mode: "index", filter: (i) => i.raw > 0 } },
        scales: { x: { stacked: true, grid: { display: false }, ticks: { autoSkip: true, maxRotation: 0 } }, y: { stacked: true, beginAtZero: true, ticks: { precision: 0 } } },
      },
    });
  }

  function renderCauses(rs) {
    const days = unique(rs.map((r) => r.date));
    const lgu = lguOnly(rs);
    const rows = [...countBy(days, (d) => H.days[d].cause)].sort((a, b) => b[1] - a[1]);
    draw("c-cause", {
      type: "bar",
      data: {
        labels: rows.map(([c]) => c),
        datasets: [{ label: "School days", data: rows.map(([, n]) => n), backgroundColor: rows.map(([c]) => causeColor(c)), borderRadius: 3 }],
      },
      options: {
        indexAxis: "y", responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { callbacks: { afterLabel: (i) => `${fmt(lgu.filter((r) => r.cause === rows[i.dataIndex][0]).length)} LGU suspensions` } } },
        scales: { x: { beginAtZero: true, ticks: { precision: 0 } }, y: { grid: { display: false } } },
      },
    });
  }

  function renderSeason() {
    // Average per calendar month over complete years, for the selected region (the year filter doesn't apply)
    const base = RECS.filter((r) => FULL_YEARS.includes(r.year) && inRegion(r));
    const n = FULL_YEARS.length || 1;
    const avgDays = MON.map((_, i) => unique(base.filter((r) => +r.date.slice(5, 7) === i + 1).map((r) => r.date)).length / n);
    const avgLgu = MON.map((_, i) => lguOnly(base).filter((r) => +r.date.slice(5, 7) === i + 1).length / n);
    const today = phToday();
    const curMonth = +today.slice(5, 7);
    const ahead = (i) => i + 1 >= curMonth;
    $("#season-sub").textContent = `Average school days with suspensions per month${where()}, ${FULL_YEARS[0]}–${FULL_YEARS[FULL_YEARS.length - 1]}. Highlighted: the rest of ${today.slice(0, 4)}.`;
    draw("c-season", {
      type: "bar",
      data: { labels: MON, datasets: [{ label: "Average school days", data: avgDays.map((v) => +v.toFixed(1)),
        backgroundColor: MON.map((_, i) => (ahead(i) ? css("--accent") : css("--accent-soft"))), borderRadius: 3 }] },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { callbacks: { afterLabel: (i) => `${avgLgu[i.dataIndex].toFixed(0)} LGU suspensions on average` } } },
        scales: { y: { beginAtZero: true }, x: { grid: { display: false } } },
      },
    });
    const rest = MON.map((_, i) => i).filter(ahead);
    const sumDays = rest.reduce((s, i) => s + avgDays[i], 0);
    const sumLgu = rest.reduce((s, i) => s + avgLgu[i], 0);
    const busiest = avgDays.indexOf(Math.max(...avgDays));
    $("#outlook").replaceChildren(
      el("b", { text: `${MONTH_FULL[curMonth - 1]} to December: ` }),
      `in ${FULL_YEARS.join(", ")}, these months averaged about ${Math.round(sumDays)} school days with suspensions and ${fmt(Math.round(sumLgu))} LGU suspensions${where()}. `,
      `The busiest month on record is ${MONTH_FULL[busiest]} (${avgDays[busiest].toFixed(1)} days a year). This is a historical average, not a forecast.`);
  }

  // ── Tables ────────────────────────────────────────────────────────────────
  function renderProvinces(rs) {
    const rows = provinceRows(rs);
    const key = state.provSort;
    rows.sort((a, b) => (typeof a[key] === "string" ? a[key].localeCompare(b[key]) * -state.provDir : (a[key] - b[key]) * state.provDir) || b.days - a.days);
    const max = Math.max(1, ...rows.map((r) => r.days));
    const cols = [["province", "Province", false], ["days", "School days", true], ["wideDays", "Whole province", true],
      ["towns", "Cities & towns", true], ["lgu", "LGU suspensions", true], ["cause", "Main cause", false]];
    const head = el("tr", {}, el("th", { class: "rank", text: "#" }), ...cols.map(([k, label, num]) => {
      const th = el("th", { class: num ? "num" : "", text: label, scope: "col", "aria-sort": state.provSort === k ? (state.provDir < 0 ? "descending" : "ascending") : "none" });
      th.addEventListener("click", () => { state.provDir = state.provSort === k ? -state.provDir : -1; state.provSort = k; renderProvinces(filtered()); });
      return th;
    }));
    const shown = state.provAll ? rows : rows.slice(0, PROV_SHOWN);
    const body = shown.map((r, i) => el("tr", {},
      el("td", { class: "rank", text: String(i + 1) }),
      el("td", {}, r.province, el("span", { class: "sub", text: r.region })),
      el("td", { class: "num" }, el("span", { class: "bar", style: `width:${Math.round((60 * r.days) / max)}px` }), String(r.days)),
      el("td", { class: "num", text: String(r.wideDays) }),
      el("td", { class: "num", text: String(r.towns) }),
      el("td", { class: "num", text: fmt(r.lgu) }),
      el("td", {}, el("span", { class: "cause-dot", style: `background:${causeColor(r.cause)}` }), r.cause)));
    $("#t-prov").replaceChildren(el("thead", {}, head), el("tbody", {}, ...body));
    $("#prov-more").hidden = rows.length <= PROV_SHOWN;
    $("#prov-more").textContent = state.provAll ? "Show top 15" : `Show all ${rows.length} provinces`;
  }

  function renderEvents(rs) {
    const days = unique(rs.map((r) => r.date)).sort();
    const events = [];
    days.forEach((d) => {
      const info = H.days[d];
      const storm = info.storms[0];
      const prev = events[events.length - 1];
      const near = prev && (new Date(d) - new Date(prev.dates[prev.dates.length - 1])) / 864e5 <= 4;
      if (prev && near && (storm ? prev.storm === storm : !prev.storm && prev.cause === info.cause)) prev.dates.push(d);
      else if (storm && events.some((e) => e.storm === storm && (new Date(d) - new Date(e.dates[e.dates.length - 1])) / 864e5 <= 7)) events.find((e) => e.storm === storm).dates.push(d);
      else events.push({ storm, cause: info.cause, dates: [d] });
    });
    events.forEach((e) => {
      const set = new Set(e.dates);
      const recs = rs.filter((r) => set.has(r.date));
      e.lgu = lguOnly(recs).length;
      e.provinces = unique(recs.map((r) => r.province).filter(Boolean)).length;
    });
    const top = events.sort((a, b) => b.lgu - a.lgu).slice(0, 10);
    const span = (ds) => {
      const a = ds[0], z = ds[ds.length - 1];
      const md = (d) => `${MON[+d.slice(5, 7) - 1]} ${+d.slice(8, 10)}`;
      if (a === z) return `${md(a)}, ${a.slice(0, 4)}`;
      if (a.slice(0, 7) === z.slice(0, 7)) return `${md(a)}–${+z.slice(8, 10)}, ${z.slice(0, 4)}`;
      return `${md(a)} – ${md(z)}, ${z.slice(0, 4)}`;
    };
    $("#t-events").replaceChildren(
      el("thead", {}, el("tr", {}, el("th", { text: "Event" }), el("th", { class: "num", text: "School days" }), el("th", { class: "num", text: "LGU suspensions" }), el("th", { class: "num", text: "Provinces" }))),
      el("tbody", {}, ...top.map((e) => el("tr", {},
        el("td", {}, el("span", { class: "cause-dot", style: `background:${causeColor(e.cause)}` }), e.storm || (e.cause === "Typhoon / tropical storm" ? "Storm (name not given)" : e.cause),
          el("span", { class: "sub", text: `${span(e.dates)}${e.storm ? ` · ${e.cause.toLowerCase()}` : ""}` })),
        el("td", { class: "num", text: String(e.dates.length) }),
        el("td", { class: "num", text: fmt(e.lgu) }),
        el("td", { class: "num", text: String(e.provinces) })))));
  }

  function renderLevels(rs) {
    const lgu = lguOnly(rs);
    const n = lgu.length;
    const rows = [
      ["Every level, preschool to college", lgu.filter((r) => r.mask === 31).length, "The whole school system closed for the day."],
      ["Basic education only (preschool to senior high)", lgu.filter((r) => r.mask === 15).length, "Colleges and universities decided on their own."],
      ["Some levels only", lgu.filter((r) => r.mask > 0 && r.mask !== 31 && r.mask !== 15).length, "Usually the youngest learners first, e.g. preschool to elementary."],
      ["Face-to-face classes only", lgu.filter((r) => r.inPerson).length, "Learning moved to modular or online instead of stopping."],
      ["Public schools only", lgu.filter((r) => r.sector === "public").length, "Private schools were left to decide."],
      ["Part of the day", lgu.filter((r) => r.partDay).length, "For example, classes suspended from 1 PM."],
      ["Levels not stated", lgu.filter((r) => r.mask < 0).length, "The announcement didn't say which levels."],
    ];
    $("#levels").replaceChildren(...rows.map(([label, k, note]) => el("div", { class: "bar-row" },
      el("p", {}, el("span", { text: label }), el("span", { text: `${pct(k, n)}% · ${fmt(k)}` })),
      el("div", { class: "track" }, el("i", { style: `width:${n ? (100 * k) / n : 0}%` })),
      el("small", { text: note }))));
  }

  // ── Now and next (live checker) ───────────────────────────────────────────
  function renderNow() {
    const box = $("#now-days");
    if (!LIVE) {
      $("#now-checked").textContent = "The live checker's results couldn't be loaded.";
      box.replaceChildren();
      return;
    }
    const today = phToday();
    const age = Date.now() - new Date(LIVE.checked_at).getTime();
    $("#now-checked").textContent = `From the news, checked ${timeText(LIVE.checked_at)}. Updates every 30 minutes in the evening and early morning.`;
    const issues = [];
    if (age > 2 * 3600 * 1000) issues.push(`The checker hasn't run since ${timeText(LIVE.checked_at)}, so newer announcements may be missing.`);
    if ((LIVE.problems || []).some((p) => p.startsWith("AI couldn't read"))) issues.push("Some new articles haven't been read yet.");
    $("#now-banner").hidden = !issues.length;
    $("#now-banner").textContent = issues.join(" ");

    const active = (LIVE.records || []).filter((r) => ACTIVE.has(r.status));
    const dates = unique([today, addDays(today, 1), ...active.map((r) => r.date).filter((d) => d > addDays(today, 1))]).sort();
    box.replaceChildren(...dates.map((d) => {
      const rs = active.filter((r) => r.date === d);
      const label = d === today ? "Today" : d === addDays(today, 1) ? "Tomorrow" : dayLabel(d, { weekday: "long" });
      const areas = unique(rs.map((r) => (r.scope === "national" ? "Nationwide" : r.scope === "region" ? r.region : r.province)).filter(Boolean));
      return el("div", { class: `now-day${rs.length ? " has" : ""}` },
        el("p", { class: "when", text: `${label} · ${dayLabel(d, { month: "short", day: "numeric" })}` }),
        el("p", { class: "n", text: rs.length ? plural(rs.length, "place") : "None announced" }),
        el("p", { class: "where", text: rs.length ? `${areas.slice(0, 6).join(", ")}${areas.length > 6 ? ` and ${areas.length - 6} more` : ""}` : "No suspension in the news so far." }));
    }));
  }

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
