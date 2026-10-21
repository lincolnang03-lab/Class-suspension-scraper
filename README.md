# Walang Pasok?

Is there class tomorrow? A page where a parent picks their city or town and sees whether
classes are suspended today or tomorrow, built from Philippine news reports.

## How it works

Every 30 minutes in the evening and early morning (hourly during the day, Philippine time),
a GitHub Action runs `python live.py run`, which:

1. Lists every article Rappler, Bombo Radyo and SunStar published yesterday and today.
2. Downloads the text of anything new or edited since the last check.
3. Skips articles that don't talk about classes being suspended, cancelled or moved online
   (a free text check; tested on 2024 data, it kept every suspension article and skipped ~80%).
4. Has Claude read the rest and pull out each place, date, school levels and status, with a
   word-for-word quote as proof.
5. Combines everything into `live/data.json`: one record per place and date, with how sure we
   are (how many and which outlets reported it), and commits it back here.

The page (`trial_dashboard/`) reads `live/data.json` from this repo and `places.json` (every
city and town, from PSGC) to match a parent's town against city, province-wide, region-wide
and nationwide suspensions. Independent cities aren't covered by their province's suspension,
except in Metro Manila.

Article text is never stored in this repo: only titles, links, the AI's reads and the one-line
quotes.

## Cost

Only new or edited articles that pass the free check are sent to the AI: about 1–5¢ on a
normal day, under $1 on a heavy typhoon day. `live/spend.json` keeps a running total per month.

## Setup

- Repository secret `ANTHROPIC_API_KEY` (Settings → Secrets and variables → Actions).
- `python live.py places` rebuilds the town list (rarely needed).
- `trial_pipeline.py` and `accuracy_check.py` are the month-by-month tools used to test
  accuracy against past data.
