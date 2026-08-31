# strongStocks

**Volume Pivot Screen** — a self-updating site of **> $20 US stocks** that show:

- **at least two consecutive weeks of above-average volume** — each 5-session week averages ≥ 1.5× the
  ~10-week baseline (the baseline is measured *before* the lookback window, so a fresh surge can't inflate
  it). The streak can be 2, 3, 4+ weeks — the longer it is, the higher the name ranks.
- a **fresh last-day volume pop** — the most recent session ≥ 1.3× its trailing 20-session average,

sitting **near their breakout pivot**. Each name gets a price line + volume-bar chart (last ~70 sessions;
blue bars mark days ≥ 1.5× the baseline; dashed line is the pivot).

Names that clear the streak but whose last-day pop is muted (or that popped today with only a 1-week streak)
land in a **"Watch"** tier instead of the main list.

Data comes from the public [hawkeyecharts.com](https://hawkeyecharts.com) pattern API (Stage-2 Pocket Pivot,
Episodic Pivot, Power Trend, 52-Week High Breakout, VCP + Vol Surge, Follow-Through Day), de-duplicated.
All volume math is computed in `build_screen.py` from the daily bars the API returns — no browser needed.

**Educational only. Not financial advice.**

## Pages produced

Every run writes into `public/`:

| File | What |
|---|---|
| `index.html` | the **latest** scan + a day-switcher linking every past scan |
| `<scan-date>.html` | that day's scan, kept forever (e.g. `2026-08-28.html`) |
| `manifest.json` | the archive record (date + hit counts per day) |

The workflow **commits `public/` back to the repo** after each run, so the per-day pages accumulate as
history. Browse them from the "Daily archive" strip at the top of any page, or in the repo under `public/`.

## How it runs

`.github/workflows/screen.yml` runs `build_screen.py` on GitHub's runners **Mon–Fri at 17:00 UTC**
(= 12:00 noon US Central during Daylight Time), commits the new day's page, and deploys `public/` to
GitHub Pages. Nothing runs on your machine.

## One-time setup

1. **Create a repository** with these files (keep the paths):
   ```
   build_screen.py
   .github/workflows/screen.yml
   README.md
   ```
   Public repo = Pages works on any plan. Private repo needs GitHub Pro for Pages.

2. **Enable Pages:** **Settings → Pages → Build and deployment → Source = "GitHub Actions".**

3. **First build:** **Actions** tab → "Build volume-pivot screen" → **Run workflow**.

4. Your site is at **`https://<your-username>.github.io/<repo-name>/`** — the exact URL is also shown
   at the bottom of each successful Actions run.

## Notes

- **DST:** the cron is fixed to 17:00 UTC. After the US "fall back" (early Nov) that is 11:00 a.m. Central.
  For noon year-round, change the cron to `0 18 * * 1-5` in winter.
- GitHub sometimes starts scheduled runs 5–30 min late under load — normal.
- GitHub **auto-disables scheduled workflows after 60 days with no repo commits.** The workflow commits a
  page most days, which keeps it awake; if the archive ever stalls, push any commit or click
  *Enable workflow* in the Actions tab.
- The workflow **fails on purpose** (nothing deployed, archive untouched) if every data feed is unreachable,
  so a broken page never replaces a good one.
- Tune the screen at the top of `build_screen.py`: `MIN_WEEKS` (streak length, default 2), `WEEK_RATIO`
  (1.5), `DAY_POP_MIN` (1.3), `MIN_PRICE` (20), `MAX_WEEKS` (how far back the streak can run).

## Run it locally

```
python3 build_screen.py out && open out/index.html
```
Only needs Python 3.9+ standard library. `out/` gets the same `index.html` + dated page + `manifest.json`.
