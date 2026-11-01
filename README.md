# Walang Pasok?

Class suspensions in the Philippines: an executive dashboard of school days lost since 2025, the
suspensions announced for today and tomorrow, and a lookup for any city or town.

## Sources: major outlets only

In this order of priority:

| Outlet | How it's read |
|---|---|
| PNA | Its latest / national / provincial / regional pages; articles read by AI |
| DepEd | Its feed (and attached advisory PDFs); articles read by AI |
| Rappler | Its daily #WalangPasok roundup, parsed **without AI**: Rappler's robots.txt opts out of AI tools |
| GMA | Its news feeds; articles read by AI |
| ABS-CBN | **Not read yet**: its site only answers web browsers, and the checker doesn't pretend to be one |
| Inquirer | **Not read yet**: its article pages are behind bot protection; the feed has headlines only |

The checker identifies itself honestly when it fetches pages (`web.UA`). See `sources.py`.

## How a check works (`python live.py run`)

1. Lists what PNA, DepEd and GMA published in the last two days.
2. Downloads anything new or edited, and skips articles that don't talk about classes being
   suspended, cancelled or moved online (a free text check; on 2024 data it kept every
   suspension article and skipped ~80% of the rest).
3. **Claude Haiku 5.5** reads each remaining article in full and copies out the passages about
   suspensions. **Claude Sonnet 5.5** turns those passages into records: place, dates, levels,
   status and a word-for-word quote, which is checked against the article (`reader.py`).
4. Parses Rappler's roundups for yesterday, today and tomorrow (no AI).
5. Merges everything into one record per place and date with a 1–5 confidence score, writes
   `live/data.json`, and republishes the site.

Confidence: 5 = three or more outlets, or PNA/DepEd plus another outlet; 4 = two outlets, or
PNA/DepEd alone; 3 = one outlet; one point less for a missing detail, a quote not found word for
word, or outlets disagreeing.

**When it runs:** once a day at 5:07 AM Philippine time, and whenever someone presses
**Check now** on the dashboard (at most once per 30 minutes and 8 times a day, since each check
costs money).

## API costs

Every Claude call is logged with its model, tokens and price in `costs/api_costs.jsonl`, with
totals by month, model and job in `costs/summary.json`. `python costs.py` prints them. Costs are
never shown on the site. Rough prices: Haiku $0.10 / $0.50 per million tokens in / out,
Sonnet $2 / $10.

## Past data

`trial_dashboard/history.json` holds every place in Rappler's roundups since January 2025
(`rappler_history.py` + `build_history.py`, free). Rebuilding 2025 onward from all the major
outlets is planned for when there's budget: `trial_pipeline.py` does it a month at a time, but
only DepEd keeps a dated archive the checker can read; PNA and GMA need another way to reach
past months.

## Setup

GitHub repository secrets (Settings → Secrets and variables → Actions):

- `ANTHROPIC_API_KEY` — the checker does nothing until this is set.
- `VERCEL_TOKEN` — lets each check republish the site (vercel.com/account/tokens).

Vercel project environment variable (Settings → Environment Variables), for the Check now button:

- `GITHUB_TOKEN` — a fine-grained GitHub token for this repository with
  **Actions: Read and write**. Without it the button says it isn't set up yet.

Other commands:

- `python sources.py` shows what each outlet lists right now (free).
- `python live.py places` rebuilds the town list (rarely needed).
- `python accuracy_check.py` compares a month's results with Rappler's and has Sonnet re-check claims.
