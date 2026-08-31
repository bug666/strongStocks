# How strongStocks works

Nothing runs on your machine and nothing runs in Claude. After setup it is entirely GitHub.

## The pieces

| Layer | What it is | Where |
|---|---|---|
| **Data** | Hawkeye's public JSON API — one call per pattern, returns each stock's daily price/volume bars, pivot, RS rank, etc. No login. | `hawkeyecharts.com/api/vcps?universe=full_market&pattern=<P>&min_vol=100000&stage2=true` |
| **Generator** | `build_screen.py` — pure Python standard library. Fetches, does all the math, writes HTML. | repo root |
| **Runner + scheduler** | GitHub Actions, defined by `screen.yml`. Runs on GitHub's servers on a cron. | `.github/workflows/screen.yml` |
| **History / storage** | The git repo itself. Each run commits the `public/` folder back. | `public/` |
| **Hosting** | GitHub Pages serves whatever the last run deployed. | `https://bug666.github.io/strongStocks/` |

## The flow, each run

```
 trigger                 GitHub Actions runner (fresh Ubuntu VM)
 ───────                 ──────────────────────────────────────
 • cron 17:00 UTC M–F    1. check out the repo (brings back public/ from last time)
 • "Run workflow" button 2. run:  python build_screen.py public
 • push to main             ├─ GET the 6 Hawkeye pattern feeds
        │                   ├─ keep price > $20, dedupe across patterns
        ▼                   ├─ per stock: compute the weekly-volume streak vs a
   ┌─────────┐              │   ~10-week baseline, and the last-day volume pop
   │ run VM  │              ├─ drop split / new-listing noise (volume 6×+ baseline)
   └─────────┘              ├─ sort into "Both legs firing" / "Watch" / out
        │                   └─ render:  public/<scan-date>.html   (kept forever)
        ▼                              public/index.html          (latest + archive strip)
                                       public/manifest.json       (the archive record)
                        3. git commit public/  →  push   (history accrues in the repo)
                        4. upload public/ as the Pages artifact  →  deploy
        │
        ▼
 https://bug666.github.io/strongStocks/   ← the new version is live ~1 min later
```

## The screen logic (step 2 above)

1. **Gather** the 6 Hawkeye pattern lists (Stage-2 Pocket Pivot, Episodic Pivot, Power Trend,
   52-Week High Breakout, VCP + Vol Surge, Follow-Through Day) → merge, drop duplicates,
   keep price > $20.
2. **Measure**, from the raw daily bars (not Hawkeye's own numbers):
   - **Streak** — walk back in fixed 5-session weeks; count how many *consecutive* recent weeks
     averaged ≥ `WEEK_RATIO` (1.5×) the `~10-week baseline` volume. The baseline window sits
     *before* the lookback weeks so a fresh surge can't inflate its own reference.
   - **Last-day pop** — most recent session ÷ its trailing 20-session average.
3. **Classify:**
   - streak ≥ `MIN_WEEKS` (2) **and** pop ≥ `DAY_POP_MIN` (1.3×) → **Both volume legs firing**
   - streak ≥ 2 with a soft pop (1.1–1.3×), or a strong pop on a 1-week streak → **Watch**
   - 2-week volume ≥ `NOISE_WK` (6×) baseline, or a single day ≥ `NOISE_DAY` (10×) →
     dropped as a data anomaly (split / new listing / buyout)
4. **Rank** by streak length, then pop size, then closeness to pivot.
5. **Render** a card per name (readout grid + inline SVG price/volume chart) into the page.

All thresholds are constants at the top of `build_screen.py`.

## Pages the generator produces

Written into `public/` every run:

| File | What |
|---|---|
| `index.html` | the **latest** scan + a "Daily archive" strip linking every past scan |
| `<scan-date>.html` | that day's scan, kept forever (e.g. `2026-08-28.html`) |
| `manifest.json` | the archive record (date + hit counts per day); the generator reads it each run to rebuild the archive strip without parsing old HTML |

## Why it's built this way

- **GitHub Actions, not a Claude scheduled cloud agent** — the Claude cloud sandbox blocks outbound
  internet to sites like Hawkeye (allowlist = package registries + Anthropic APIs only). GitHub's
  runners have open internet.
- **Commit `public/` back to the repo** — GitHub Pages keeps only the *last* deploy, so the commit
  is what turns the per-day pages into a permanent archive. It also keeps the scheduled workflow
  "active" (GitHub pauses schedules after 60 days with no commits).
- **`push` trigger with `paths-ignore: public/**`** — editing the script and pushing rebuilds
  immediately; the bot's own archive commits are ignored so it never loops.
- **Fail-closed** — if every data feed is unreachable the run exits non-zero and deploys nothing,
  so a broken page never replaces a good one.

## Preview an off-Pages version of a page

GitHub's file view shows HTML source, not rendered. The deployed pages already render at the Pages
URL. For a page on another branch/commit/PR, use a proxy that sets the right content-type:

- `https://raw.githack.com/bug666/strongStocks/<ref>/public/<file>.html`
- `https://htmlpreview.github.io/?https://github.com/bug666/strongStocks/blob/<ref>/public/<file>.html`
