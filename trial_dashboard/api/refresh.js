// "Check now" on the dashboard: starts the news check (the GitHub Actions workflow) on demand.
//   GET  -> is a check running, and when did the last one finish
//   POST -> start a check, unless one is running, one ran in the last 30 minutes,
//           or 8 have been started from here in the last 24 hours (each check costs money)
// Needs GITHUB_TOKEN in the Vercel project's environment variables: a fine-grained token for the
// repo with "Actions: Read and write". GITHUB_REPO overrides the repo if it moves.

const REPO = process.env.GITHUB_REPO || "lincolnang03-lab/Class-suspension-scraper";
const WORKFLOW = "check.yml";
const COOLDOWN_MIN = 30;
const MAX_PER_DAY = 8;

async function gh(path, options = {}) {
  return fetch(`https://api.github.com/repos/${REPO}${path}`, {
    ...options,
    headers: {
      Authorization: `Bearer ${process.env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "walang-pasok-dashboard",
      ...(options.body ? { "Content-Type": "application/json" } : {}),
    },
  });
}

async function recentRuns() {
  const r = await gh(`/actions/workflows/${WORKFLOW}/runs?per_page=30`);
  if (!r.ok) throw new Error(`GitHub said ${r.status}`);
  return (await r.json()).workflow_runs || [];
}

module.exports = async (req, res) => {
  res.setHeader("Cache-Control", "no-store");
  if (!process.env.GITHUB_TOKEN) {
    return res.status(503).json({ state: "off", message: "The Check now button isn't set up yet." });
  }
  try {
    const runs = await recentRuns();
    const running = runs.find((r) => r.status !== "completed");
    const last = runs.find((r) => r.status === "completed");
    const status = {
      running: Boolean(running),
      started_at: running ? running.run_started_at || running.created_at : null,
      last_finished: last ? last.updated_at : null,
      last_ok: last ? last.conclusion === "success" : null,
    };
    if (req.method === "GET") return res.status(200).json({ state: running ? "running" : "idle", ...status });
    if (req.method !== "POST") return res.status(405).json({ message: "Use GET or POST." });

    if (running) return res.status(200).json({ state: "running", ...status, message: "A check is already running." });
    const sinceLast = last ? (Date.now() - Date.parse(last.updated_at)) / 60000 : Infinity;
    if (sinceLast < COOLDOWN_MIN) {
      const wait = Math.ceil(COOLDOWN_MIN - sinceLast);
      return res.status(429).json({ state: "cooldown", ...status,
        message: `The news was checked ${Math.floor(sinceLast)} min ago. You can check again in ${wait} min.` });
    }
    const today = runs.filter((r) => r.event === "workflow_dispatch" && Date.now() - Date.parse(r.created_at) < 864e5);
    if (today.length >= MAX_PER_DAY) {
      return res.status(429).json({ state: "limit", ...status,
        message: `The news has been checked ${today.length} times in the last 24 hours, the daily limit. Try again later.` });
    }
    const r = await gh(`/actions/workflows/${WORKFLOW}/dispatches`, { method: "POST", body: JSON.stringify({ ref: "main" }) });
    if (r.status !== 204) throw new Error(`GitHub said ${r.status}`);
    return res.status(202).json({ state: "started", ...status, running: true,
      message: "Checking the news now. This takes about 3 to 5 minutes." });
  } catch (e) {
    return res.status(502).json({ state: "error", message: `Couldn't reach GitHub (${e.message}). Try again in a minute.` });
  }
};
