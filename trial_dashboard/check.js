/* Walang Pasok? — pick your town and a day, get one answer. Reads the live checker's data.json and places.json. */
(() => {
  const LEVELS = [
    ["preschool", "Preschool"], ["elementary", "Elementary"], ["junior_high", "Junior High"],
    ["senior_high", "Senior High"], ["college", "College"],
  ];
  const ACTIVE = new Set(["suspended", "online_or_modular", "opening_postponed"]);
  const STATUS_TEXT = {
    suspended: "Classes suspended",
    online_or_modular: "Classes moved online / modular",
    opening_postponed: "School opening postponed",
    lifted: "Suspension lifted",
  };
  const SCOPE_RANK = { city: 0, province: 1, region: 2, national: 3 };
  const REFRESH_MS = 5 * 60 * 1000;
  const STALE_MS = 26 * 60 * 60 * 1000;  // checks run daily

  const $ = (s) => document.querySelector(s);
  const el = (tag, props = {}, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else n.setAttribute(k, v);
    }
    n.append(...kids.filter((k) => k !== null && k !== undefined && k !== false));
    return n;
  };
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { v ? localStorage.setItem(k, v) : localStorage.removeItem(k); } catch { /* private mode */ } },
  };

  // Same place matching as the scraper (place_key in trial_pipeline.py)
  const ALIASES = { montalban: "rodriguez", "general santos": "gensan" };
  const norm = (s) => (s || "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase()
    .replace(/[‘’“”'"`]/g, "").replace(/[^a-z0-9 ]/g, " ").replace(/\s+/g, " ").trim();
  const placeKey = (s) => {
    const k = norm(s).replace(/^city of /, "").replace(/ city$/, "")
      .replace(/^(municipality|town) of /, "").replace(/ (town|municipality)$/, "");
    return ALIASES[k] || k;
  };

  // Dates and times in Philippine time, whatever the phone's own setting
  const phDate = (d = new Date()) => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Manila" }).format(d);
  const phHour = () => +new Intl.DateTimeFormat("en-US", { timeZone: "Asia/Manila", hour: "numeric", hourCycle: "h23" }).format(new Date());
  const addDays = (iso, n) => { const d = new Date(`${iso}T00:00:00Z`); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
  const dayName = (iso, opts) => new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", { timeZone: "UTC", ...opts });
  const timeText = (iso) => new Date(iso).toLocaleString("en-US", { timeZone: "Asia/Manila", hour: "numeric", minute: "2-digit" });
  const whenText = (iso) => {
    const d = new Date(iso.endsWith("Z") || /[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);
    const day = phDate(d) === phDate() ? "today" : phDate(d) === addDays(phDate(), -1) ? "yesterday" : dayName(phDate(d), { month: "short", day: "numeric" });
    return `${timeText(d)} ${day}`;
  };

  const state = { place: null, level: store.get("wp-level") || "", day: "" };
  let PLACES = [];          // {region, province, name, isCity, independent, label}
  let DATA = null;
  let uniqueNames = new Set();

  // ── Load ──────────────────────────────────────────────────────────────────
  Promise.all([fetch("places.json").then((r) => r.json()), loadData()])
    .then(([places]) => {
      PLACES = places.places.map(([region, province, name, isCity, independent]) =>
        ({ region, province, name, isCity: !!isCity, independent: !!independent, label: `${name}, ${province}` }));
      const counts = new Map();
      PLACES.forEach((p) => counts.set(placeKey(p.name), (counts.get(placeKey(p.name)) || 0) + 1));
      uniqueNames = new Set([...counts].filter(([, n]) => n === 1).map(([k]) => k));
      $("#places").replaceChildren(...PLACES.map((p) => el("option", { value: p.label })));
      // A shared link (?place=Malolos City, Bulacan&day=tomorrow) wins over the place saved on this phone
      const params = new URLSearchParams(location.search);
      const wanted = (params.get("place") || store.get("wp-place") || "").toLowerCase();
      state.place = PLACES.find((p) => p.label.toLowerCase() === wanted) || null;
      if (state.place) { $("#place").value = state.place.label; store.set("wp-place", state.place.label); }
      const dayParam = params.get("day");
      state.day = dayParam === "today" ? phDate() : dayParam === "tomorrow" ? addDays(phDate(), 1)
        : phHour() >= 12 ? addDays(phDate(), 1) : phDate();
      bind();
      render();
      setInterval(() => loadData().then(render), REFRESH_MS);
    })
    .catch((e) => { $("#checked").textContent = `Couldn't load the latest check (${e.message}). Try again in a minute.`; });

  function loadData() {
    const urls = [window.LIVE_DATA_URL, "data.json"].filter(Boolean);
    const attempt = (i) => fetch(`${urls[i]}?t=${Date.now()}`, { cache: "no-store" })
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .catch((e) => (i + 1 < urls.length ? attempt(i + 1) : Promise.reject(e)));
    return attempt(0).then((d) => { DATA = d; });
  }

  // ── Controls ──────────────────────────────────────────────────────────────
  function bind() {
    const pick = () => {
      const v = $("#place").value.trim().toLowerCase();
      const found = PLACES.find((p) => p.label.toLowerCase() === v);
      if (found || !v) {
        state.place = found || null;
        store.set("wp-place", found ? found.label : "");
        render();
      }
    };
    $("#place").addEventListener("change", pick);
    $("#place").addEventListener("input", () => { if (PLACES.some((p) => p.label === $("#place").value)) pick(); });
    $("#place").addEventListener("focus", (e) => e.target.select());
  }

  function renderControls() {
    $("#levels").replaceChildren(...[["", "Any"], ...LEVELS].map(([k, label]) => {
      const b = el("button", { type: "button", class: "chip", "aria-pressed": String(state.level === k), text: label });
      b.addEventListener("click", () => { state.level = k; store.set("wp-level", k); render(); });
      return b;
    }));
    const today = phDate();
    $("#days").replaceChildren(...[[today, "Today"], [addDays(today, 1), "Tomorrow"]].map(([iso, word]) => {
      const b = el("button", { type: "button", class: "day", "aria-pressed": String(state.day === iso) },
        el("b", { text: word }), el("span", { text: dayName(iso, { weekday: "short", month: "short", day: "numeric" }) }));
      b.addEventListener("click", () => { state.day = iso; render(); });
      return b;
    }));
  }

  // ── Which records apply to a place ────────────────────────────────────────
  function applies(r, p) {
    const prov = placeKey(r.province), city = placeKey(r.city);
    switch (r.scope) {
      case "city":
        return city === placeKey(p.name) && (prov === placeKey(p.province) || !r.province || uniqueNames.has(placeKey(p.name)));
      case "province":
        // A governor's suspension doesn't reach an independent city, except in Metro Manila
        if (prov === placeKey(p.name) && p.isCity) return true; // e.g. "Olongapo City" given as its own province
        return prov === placeKey(p.province) && (!p.independent || p.province === "Metro Manila");
      case "region":
        return r.region === p.region;
      case "national":
        return true;
      default:
        return false;
    }
  }

  const dayRecords = () => (DATA ? DATA.records.filter((r) => r.date === state.day) : []);

  // ── Header: last check + problems ─────────────────────────────────────────
  function renderStatus() {
    if (!DATA) return;
    const age = Date.now() - new Date(DATA.checked_at).getTime();
    $("#checked").replaceChildren("Last checked ", el("b", { text: whenText(DATA.checked_at) }), ". Checked every morning at 5 AM.");
    const issues = [];
    if (age > STALE_MS) issues.push(`The checker hasn't run since ${whenText(DATA.checked_at)}, so newer announcements may be missing.`);
    if ((DATA.problems || []).some((p) => p.startsWith("AI couldn't read"))) issues.push("Some new articles haven't been read yet, so the newest announcements may be missing.");
    $("#banner").hidden = !issues.length;
    $("#banner").textContent = issues.join(" ");
    $("#outlets").textContent = (DATA.outlets || []).join(", ");
  }

  // ── The answer for one place ──────────────────────────────────────────────
  function levelsText(r) {
    if (!r.levels) return "Levels not stated";
    const on = LEVELS.filter(([k]) => r.levels[k]).map(([, l]) => l);
    if (on.length === LEVELS.length) return "All levels";
    return on.length ? on.join(", ") : "Levels not stated";
  }
  const sectorText = (r) => ({ "Public & private": "public and private schools", "Public only": "public schools only",
    "Private only": "private schools only" }[r.sector] || "public/private not stated");
  const sureText = (r) => (r.confidence >= 4 ? ["strong", "Strong"] : r.confidence === 3 ? ["good", "Good"] : ["weak", "Check it"]);
  const whereText = (r, p) => ({
    city: `${p.name}`,
    province: r.province === "Metro Manila" ? "all of Metro Manila" : `all of ${r.province}`,
    region: `the whole ${r.region} region`,
    national: "the whole country",
  }[r.scope]);

  function renderAnswer() {
    const box = $("#answer");
    const day = dayName(state.day, { weekday: "long", month: "long", day: "numeric" });
    const recs = dayRecords();
    if (!state.place) {
      const n = recs.filter((r) => ACTIVE.has(r.status)).length;
      box.className = "answer idle";
      box.replaceChildren(el("p", { class: "big", text: "Pick your city or town above." }),
        el("p", { text: n ? `${n} ${n === 1 ? "place has" : "places have"} suspensions on ${day}. See the list below.` : `No suspensions found for ${day} so far.` }));
      return;
    }
    const p = state.place;
    const mine = recs.filter((r) => applies(r, p)).sort((a, b) => SCOPE_RANK[a.scope] - SCOPE_RANK[b.scope]);
    const active = mine.filter((r) => ACTIVE.has(r.status));
    const lifted = mine.filter((r) => r.status === "lifted");
    const someSchools = recs.filter((r) => r.scope === "schools" && placeKey(r.province) === placeKey(p.province) && ACTIVE.has(r.status));

    const parts = [el("p", { class: "for", text: `${p.name}, ${p.province} · ${day}` })];
    if (active.length) {
      const main = active[0];
      const levelOk = !state.level || !main.levels || main.levels[state.level];
      const levelName = state.level && LEVELS.find(([k]) => k === state.level)[1];
      box.className = `answer ${levelOk ? "yes" : "partial"}`;
      parts.push(el("p", { class: "big", text: levelOk ? STATUS_TEXT[main.status] : `Not for ${levelName}` }));
      if (!levelOk) parts.push(el("p", { class: "lead", text: `${STATUS_TEXT[main.status]} for ${levelsText(main)} only. ${levelName} is not included.` }));
      else if (state.level && !main.levels) parts.push(el("p", { class: "lead", text: `The announcement doesn't say which levels, so check whether ${levelName} is included.` }));
      active.forEach((r) => parts.push(detail(r, p)));
    } else if (lifted.length) {
      box.className = "answer lifted";
      parts.push(el("p", { class: "big", text: "Suspension lifted" }), el("p", { class: "lead", text: "An earlier suspension was called off." }));
      lifted.forEach((r) => parts.push(detail(r, p)));
    } else {
      box.className = "answer none";
      parts.push(el("p", { class: "big", text: "No suspension announced yet" }),
        el("p", { class: "lead", text: DATA ? `Nothing in the news for ${p.name} as of ${whenText(DATA.checked_at)}. Suspensions are usually announced the night before or by 5 AM, so check again later, and watch your city's official page.` : "" }));
    }
    if (someSchools.length) {
      parts.push(el("p", { class: "aside", text: `Some schools in ${p.province} have their own suspensions: ${someSchools.map((r) => r.suspension_info).join("; ")}.` }));
    }
    box.replaceChildren(...parts);
  }

  function detail(r, p) {
    const [cls, sure] = sureText(r);
    const latest = r.sources.reduce((a, s) => (s.time > a.time ? s : a), r.sources[0]);
    const outlets = [...new Map(r.sources.map((s) => [s.outlet, s.url])).entries()];
    return el("div", { class: "detail" },
      el("p", { class: "what" }, el("b", { text: `${levelsText(r)}, ${sectorText(r)}` }), ` · for ${whereText(r, p)}`),
      r.notes.length ? el("p", { class: "notes" }, ...r.notes.map((n) => el("span", { class: "note", text: n }))) : null,
      el("blockquote", { text: `“${latest.quote}”` }),
      el("p", { class: "meta" },
        el("span", { class: `sure ${cls}`, text: sure, title: r.confidence_reason }), " · ",
        ...outlets.flatMap(([o, url], i) => [i ? ", " : "", el("a", { href: url, target: "_blank", rel: "noopener", text: o })]),
        ` · reported ${whenText(latest.time)}`));
  }

  // ── Everything for the day ────────────────────────────────────────────────
  function renderList() {
    const recs = dayRecords().filter((r) => ACTIVE.has(r.status) || r.status === "lifted");
    $("#list-title").textContent = `All suspensions · ${dayName(state.day, { weekday: "short", month: "short", day: "numeric" })}`;
    $("#list-sub").textContent = recs.length ? `${recs.length} ${recs.length === 1 ? "place" : "places"}, by province.` : "";
    if (!recs.length) {
      $("#list").replaceChildren(el("p", { class: "muted pad", text: "None found in the news yet for this day." }));
      return;
    }
    const groupOf = (r) => r.scope === "national" ? "Nationwide" : r.scope === "region" ? `${r.region} (whole region)` : r.province || "Province not stated";
    const groups = new Map();
    recs.forEach((r) => { const g = groupOf(r); if (!groups.has(g)) groups.set(g, []); groups.get(g).push(r); });
    const order = [...groups.keys()].sort((a, b) => (a === "Nationwide" ? -1 : b === "Nationwide" ? 1 : a.localeCompare(b)));
    $("#list").replaceChildren(...order.map((g) => el("div", { class: "group" },
      el("h3", { text: g }),
      el("ul", {}, ...groups.get(g).sort((a, b) => (a.scope === "city") - (b.scope === "city") || (a.city || "").localeCompare(b.city || "")).map((r) => {
        const name = r.scope === "city" ? r.city : r.scope === "schools" ? "Some schools" : r.scope === "province" ? `All of ${r.province}` : "Whole area";
        const [cls, sure] = sureText(r);
        const src = r.sources[0];
        return el("li", { class: r.status === "lifted" ? "is-lifted" : "" },
          el("span", { class: "name", text: name }),
          el("span", { class: "st", text: r.status === "suspended" ? levelsText(r) : STATUS_TEXT[r.status] || r.status }),
          el("span", { class: `sure ${cls}`, text: sure, title: r.confidence_reason }),
          el("a", { href: src.url, target: "_blank", rel: "noopener", text: src.outlet }));
      })))));
  }

  function render() {
    renderControls();
    renderStatus();
    renderAnswer();
    renderList();
  }
})();
