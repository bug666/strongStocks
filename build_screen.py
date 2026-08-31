#!/usr/bin/env python3
"""
Hawkeye volume-breakout screen.

Pulls Hawkeye's JSON API for a set of volume/breakout patterns, then keeps names that show:
  - price > $20
  - AT LEAST 2 consecutive recent weeks of above-average volume
    (each week's avg volume >= 1.5x the ~10-week baseline; the streak may be 2, 3, 4+ weeks)
  - a fresh last-day volume pop (last session >= 1.3x the prior 20-session average)
Ranks by the length of the elevated-volume streak, then the last-day pop, then proximity to the pivot.

Writes one page per scan date plus a rolling "index.html" (latest scan + a link list of every past day).

No browser needed - the API is plain HTTP GET.
"""

import json, sys, time, urllib.request, datetime, html, math, os

PATTERNS = ["stage2_pp", "episodic_pivot", "power_trend", "52wh", "vcp2", "ftd"]
API = "https://hawkeyecharts.com/api/vcps?universe=full_market&pattern={p}&min_vol=100000&stage2=true"

MIN_PRICE     = 20.0
WEEK_RATIO    = 1.5    # a week counts as "elevated" when its avg volume >= this * baseline
MIN_WEEKS     = 2      # require a streak of at least this many consecutive elevated weeks
MAX_WEEKS     = 8      # how many recent weeks to look back over
DAY_POP_MIN   = 1.3    # last session vs prior 20-session average
NEAR_MISS_DAY = 1.10
NOISE_WK      = 6.0    # 2-week volume this far above baseline = split / new listing / buyout, not accumulation
NOISE_DAY     = 10.0


def fetch(pat, raw_cache=None):
    if raw_cache and pat in raw_cache:
        return raw_cache[pat]
    url = API.format(p=pat)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 hawkeye-screen"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def vol_stats(v):
    """v: list of daily volumes, oldest -> newest. Returns dict or None.

    Weeks are fixed 5-session blocks counted back from the most recent bar.
    'weeks' is the length of the unbroken run of recent weeks whose average
    volume is >= WEEK_RATIO * baseline, starting from this week and walking back.
    baseline = mean volume over the ~10 weeks that sit *before* the lookback
    window, so a fresh surge can't inflate its own baseline.
    """
    v = [x for x in v if isinstance(x, (int, float)) and x >= 0]
    need = 5 * MAX_WEEKS + 50
    if len(v) < need:
        return None
    base = v[-(5 * MAX_WEEKS + 50):-(5 * MAX_WEEKS)]
    base_avg = sum(base) / len(base)
    if base_avg <= 0:
        return None

    week_ratios = []
    for k in range(1, MAX_WEEKS + 1):
        wk = v[-5 * k: (-5 * (k - 1) or None)]
        week_ratios.append((sum(wk) / len(wk)) / base_avg)

    streak = 0
    for wr in week_ratios:
        if wr >= WEEK_RATIO:
            streak += 1
        else:
            break

    last20 = v[-21:-1]
    return {
        "weeks":       streak,                          # consecutive elevated weeks, most-recent first
        "week_ratios": week_ratios,                     # ratio vs baseline for each of the last MAX_WEEKS weeks
        "wk2_ratio":   sum(week_ratios[:2]) / 2,        # avg of the last two weeks (used for the noise cut)
        "day_pop":     v[-1] / (sum(last20) / len(last20)) if last20 else None,
        "base_avg":    base_avg,
    }


def collect(raw_cache=None):
    by_ticker = {}
    meta = {"noise_excluded": set(), "errs": []}
    for pat in PATTERNS:
        try:
            j = fetch(pat, raw_cache)
        except Exception as e:
            sys.stderr.write(f"{pat}: {e}\n")
            meta["errs"].append(f"{pat}: {e}")
            continue
        meta.setdefault("data_date", j.get("data_date"))
        meta.setdefault("updated_at", j.get("updated_at"))
        meta.setdefault("total_universe", j.get("total_universe"))
        for s in j.get("stocks", []):
            if not (s.get("price", 0) > MIN_PRICE):
                continue
            o = s.get("ohlc") or {}
            vs = vol_stats(o.get("v") or [])
            if not vs:
                continue
            if vs["wk2_ratio"] >= NOISE_WK or (vs["day_pop"] or 0) >= NOISE_DAY:
                meta["noise_excluded"].add(s["ticker"])
                continue
            t = s["ticker"]
            rec = by_ticker.get(t)
            if not rec:
                n = len(o.get("d", []))
                k = min(70, n)
                rec = {
                    "ticker": t,
                    "name": s.get("name", ""),
                    "sector": s.get("sector", ""),
                    "price": s.get("price"),
                    "change_pct": s.get("change_pct"),
                    "rs_rank": s.get("rs_rank"),
                    "pct_from_pivot": s.get("pct_from_pivot"),
                    "pct_from_52w_high": s.get("pct_from_52w_high"),
                    "pivot_high": s.get("pivot_high"),
                    "base_width_weeks": s.get("base_width_weeks"),
                    "perf_12m": s.get("perf_12m"),
                    "vol_accumulation": s.get("vol_accumulation"),
                    "vs": vs,
                    "patterns": set(),
                    "chart": {
                        "d": (o.get("d") or [])[n - k:],
                        "c": (o.get("c") or [])[n - k:],
                        "v": (o.get("v") or [])[n - k:],
                    },
                }
                by_ticker[t] = rec
            rec["patterns"].add(j.get("pattern_label", pat))
    return list(by_ticker.values()), meta


def tier(r):
    vs = r["vs"]
    weeks, day = vs["weeks"], vs["day_pop"] or 0
    if weeks >= MIN_WEEKS and day >= DAY_POP_MIN:
        return 1
    # one leg soft: the volume streak is there but today's pop is muted,
    # or today popped but the streak is only one week
    if weeks >= MIN_WEEKS and day >= NEAR_MISS_DAY:
        return 2
    if weeks == MIN_WEEKS - 1 and day >= DAY_POP_MIN:
        return 2
    return 0


def rank_key(r):
    vs = r["vs"]
    piv = r["pct_from_pivot"] if r["pct_from_pivot"] is not None else -99
    return (-vs["weeks"], -(vs["day_pop"] or 0), -piv)


# ---------- rendering ----------

def fnum(x, d=2):
    if x is None:
        return "–"
    return f"{x:,.{d}f}"


def spark_svg(chart, pivot, base_avg, uid):
    d, c, v = chart["d"], chart["c"], chart["v"]
    c = [x for x in c if isinstance(x, (int, float))]
    v = [x for x in v if isinstance(x, (int, float))]
    n = min(len(d), len(c), len(v))
    if n < 10:
        return "<div class='nochart'>no chart data</div>"
    d, c, v = d[-n:], c[-n:], v[-n:]
    W, H = 620, 200
    PADL, PADR, PADT = 6, 44, 8
    VOL_H, GAP = 58, 14
    price_h = H - PADT - VOL_H - GAP
    lo, hi = min(c), max(c)
    if pivot and lo <= pivot <= hi * 1.15:
        hi = max(hi, pivot)
        lo = min(lo, pivot)
    rng = (hi - lo) or 1
    iw = W - PADL - PADR
    xs = [PADL + iw * i / (n - 1) for i in range(n)]
    py = [PADT + price_h * (1 - (val - lo) / rng) for val in c]
    # clip the volume scale to the ~92nd percentile so one blow-off day doesn't flatten the rest
    sv = sorted(v)
    vmax = sv[max(0, min(len(sv) - 1, int(len(sv) * 0.92)))] or (max(v) or 1)
    bw = max(1.5, iw / n * 0.62)

    bars = []
    for i in range(n):
        bh = min(VOL_H, VOL_H * (v[i] / vmax))
        y = PADT + price_h + GAP + (VOL_H - bh)
        hot = base_avg and v[i] >= 1.5 * base_avg
        cls = "vb hot" if hot else "vb"
        bars.append(f"<rect class='{cls}' x='{xs[i]-bw/2:.1f}' y='{y:.1f}' width='{bw:.1f}' height='{max(bh,0.6):.1f}' rx='1'/>")

    line = "M" + " L".join(f"{xs[i]:.1f} {py[i]:.1f}" for i in range(n))
    endx, endy = xs[-1], py[-1]

    last_close = c[-1]
    lc_y = py[-1]

    piv_line = ""
    pvy = None
    if pivot and lo <= pivot <= hi:
        pvy = PADT + price_h * (1 - (pivot - lo) / rng)
        piv_line = (
            f"<line class='pivot' x1='{PADL}' y1='{pvy:.1f}' x2='{W-PADR}' y2='{pvy:.1f}'/>"
            f"<text class='pivlab' x='{W-PADR+3}' y='{pvy+3:.1f}'>pivot {pivot:.2f}</text>"
        )

    plab_y = lc_y + 3
    if pvy is not None and abs(plab_y - (pvy + 3)) < 11:
        plab_y = pvy + 3 + (12 if lc_y >= pvy else -12)
    price_lab = f"<text class='plab' x='{W-PADR+3}' y='{plab_y:.1f}'>{last_close:.2f}</text>"

    # sparse date ticks
    ticks = []
    for i in (0, n // 2, n - 1):
        lbl = d[i][5:] if isinstance(d[i], str) and len(d[i]) >= 10 else str(d[i])
        anchor = "start" if i == 0 else ("end" if i == n - 1 else "middle")
        ticks.append(f"<text class='xt' x='{xs[i]:.1f}' y='{H-2}' text-anchor='{anchor}'>{lbl}</text>")

    return f"""<svg viewBox="0 0 {W} {H}" class="spark" preserveAspectRatio="none" role="img" aria-label="price and volume, last {n} sessions">
  <g class="vols">{''.join(bars)}</g>
  {piv_line}
  <path class="pl" d="{line}"/>
  <circle class="pe" cx="{endx:.1f}" cy="{endy:.1f}" r="3.2"/>
  {price_lab}
  {''.join(ticks)}
</svg>"""


def card(r):
    vs = r["vs"]
    ch = r["change_pct"] or 0
    dir_cls = "up" if ch >= 0 else "down"
    piv = r["pct_from_pivot"]
    piv_cls = "at" if piv is not None and piv >= -2 else ("near" if piv is not None and piv >= -5 else "far")
    pats = "  ·  ".join(sorted(r["patterns"]))
    chart = spark_svg(r["chart"], r.get("pivot_high"), vs["base_avg"], r["ticker"])
    wr = vs["week_ratios"]
    weeks_txt = f"{vs['weeks']}+" if vs["weeks"] >= MAX_WEEKS else f"{vs['weeks']}"
    rows = [
        ("weeks ≥ 1.5×", weeks_txt, "hot" if vs["weeks"] >= MIN_WEEKS else ""),
        ("last-day pop", f"{(vs['day_pop'] or 0):.2f}×", "hot" if (vs["day_pop"] or 0) >= DAY_POP_MIN else ""),
        ("this week", f"{wr[0]:.2f}×", "hot" if wr[0] >= WEEK_RATIO else ""),
        ("1 wk ago", f"{wr[1]:.2f}×", "hot" if wr[1] >= WEEK_RATIO else ""),
        ("2 wks ago", f"{wr[2]:.2f}×", "hot" if wr[2] >= WEEK_RATIO else ""),
        ("from pivot", f"{fnum(piv,1)}%", piv_cls),
        ("from 52w high", f"{fnum(r['pct_from_52w_high'],1)}%", ""),
        ("RS rank", f"{r['rs_rank'] if r['rs_rank'] is not None else '–'}", ""),
    ]
    metric_html = "".join(
        f"<div class='m'><span class='mk'>{k}</span><span class='mv {c}'>{html.escape(v)}</span></div>"
        for k, v, c in rows
    )
    return f"""<article class="card">
  <div class="readout">
    <div class="idline">
      <span class="tkr">{html.escape(r['ticker'])}</span>
      <span class="px">${fnum(r['price'])}</span>
      <span class="chg {dir_cls}">{'+' if ch>=0 else ''}{fnum(ch,2)}%</span>
    </div>
    <div class="nm">{html.escape(r['name'] or '')}</div>
    <div class="sec">{html.escape(r['sector'] or '')}</div>
    <div class="metrics">{metric_html}</div>
    <div class="pats">{html.escape(pats)}</div>
  </div>
  <div class="chartwrap">{chart}</div>
</article>"""


def archive_nav(archive, current, is_index):
    """archive: list of {date, pass, watch}, newest first. Renders the day switcher."""
    links = []
    if not is_index:
        links.append("<a href='./index.html'>latest &rarr;</a>")
    for e in archive:
        d = e["date"]
        label = f"{d}<span class='ac'> {e.get('pass','?')}+{e.get('watch','?')}</span>"
        href = "./index.html" if (is_index and d == current) else f"./{d}.html"
        cls = " class='on'" if d == current else ""
        links.append(f"<a href='{href}'{cls}>{label}</a>")
    return ("<nav class='arch'><span class='archlbl'>Daily archive</span>"
            + "".join(links) + "</nav>")


def render(rows, meta, archive, scan_date, is_index):
    t1 = [r for r in rows if tier(r) == 1]
    t2 = [r for r in rows if tier(r) == 2]
    t1.sort(key=rank_key)
    t2.sort(key=rank_key)

    dd = meta.get("data_date") or scan_date
    gen = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    updated = ""
    if meta.get("updated_at"):
        updated = datetime.datetime.fromtimestamp(meta["updated_at"], datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def section(title, sub, items):
        if not items:
            return f"<section><h2>{title}</h2><p class='empty'>Nothing cleared this bar for this scan.</p></section>"
        return (
            f"<section><h2>{title} <span class='count'>{len(items)}</span></h2>"
            f"<p class='shdr'>{sub}</p>" + "".join(card(r) for r in items) + "</section>"
        )

    body = (
        section("Both volume legs firing",
                f"At least {MIN_WEEKS} consecutive weeks with volume &#8805; {WEEK_RATIO:g}&#215; the ~10-week baseline "
                f"<em>and</em> a last-day pop &#8805; {DAY_POP_MIN:g}&#215; the 20-day average.",
                t1)
        + section("Watch — one leg soft",
                  "The multi-week volume streak is there but today's pop is muted, or today popped "
                  f"while the streak is still {MIN_WEEKS - 1} week.",
                  t2)
    )

    noise = sorted(meta.get("noise_excluded") or [])
    noise_html = ""
    if noise:
        noise_html = ("<p class='note'>Set aside as data anomalies (2-week volume &#8805; 6&#215; baseline &#8212; "
                      "typically a split, new listing, or buyout): " + html.escape(", ".join(noise)) + ".</p>")

    title = "Volume Pivot Screen" if is_index else f"Volume Pivot Screen &middot; {html.escape(scan_date)}"
    kind = ("Latest scan" if is_index else "Archived scan") + f" &middot; {html.escape(str(dd))}"

    return TEMPLATE.format(
        title=title,
        kind=kind,
        arch=archive_nav(archive, scan_date, is_index),
        data_date=html.escape(str(dd)),
        scan_updated=html.escape(updated or "–"),
        generated=html.escape(gen),
        universe=html.escape(str(meta.get("total_universe") or "–")),
        n_pass=len(t1), n_watch=len(t2),
        body=body,
        noise=noise_html,
    )


TEMPLATE = """<title>{title}</title>
<meta name="description" content="Daily Hawkeye screen: >$20 stocks with two or more weeks of above-average volume and a fresh last-day pop near their pivot.">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
:root {{
  --ground:#f3f4f6; --panel:#ffffff; --panel-2:#f8f9fb;
  --ink:#1a1f28; --ink-2:#5a6675; --ink-3:#8b95a3;
  --hair:#e2e5ea; --hair-2:#eceef2;
  --accent:#b0812f; --accent-soft:#f0e4cd;
  --up:#137a45; --down:#c23b2c;
  --vol:#c3c9d2; --vol-hot:#3f7fa6;
  --pivot:#b0812f;
  --shadow:0 1px 2px rgba(20,26,38,.05),0 8px 24px -12px rgba(20,26,38,.12);
}}
:root:not([data-theme="light"]) {{}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --ground:#0d1117; --panel:#161b22; --panel-2:#1b212a;
    --ink:#e6edf3; --ink-2:#9aa5b1; --ink-3:#6b7684;
    --hair:#262d38; --hair-2:#20262f;
    --accent:#d6a75a; --accent-soft:#33291a;
    --up:#3fb950; --down:#e5534b;
    --vol:#39424e; --vol-hot:#4a8fb8;
    --pivot:#d6a75a;
    --shadow:0 1px 2px rgba(0,0,0,.3),0 10px 30px -14px rgba(0,0,0,.55);
  }}
}}
:root[data-theme="dark"] {{
  --ground:#0d1117; --panel:#161b22; --panel-2:#1b212a;
  --ink:#e6edf3; --ink-2:#9aa5b1; --ink-3:#6b7684;
  --hair:#262d38; --hair-2:#20262f;
  --accent:#d6a75a; --accent-soft:#33291a;
  --up:#3fb950; --down:#e5534b;
  --vol:#39424e; --vol-hot:#4a8fb8;
  --pivot:#d6a75a;
  --shadow:0 1px 2px rgba(0,0,0,.3),0 10px 30px -14px rgba(0,0,0,.55);
}}
* {{ box-sizing:border-box; }}
body {{
  margin:0; background:var(--ground); color:var(--ink);
  font-family:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  line-height:1.5; -webkit-font-smoothing:antialiased;
}}
.wrap {{ max-width:1080px; margin:0 auto; padding:40px 24px 72px; }}
header {{ border-bottom:1px solid var(--hair); padding-bottom:22px; margin-bottom:8px; }}
h1 {{
  font-size:26px; font-weight:600; letter-spacing:-.01em; margin:0 0 6px;
  text-wrap:balance;
}}
.lede {{ color:var(--ink-2); font-size:14px; max-width:60ch; margin:0 0 18px; }}
.meta {{
  display:flex; flex-wrap:wrap; gap:6px 22px; font-family:"IBM Plex Mono",ui-monospace,monospace;
  font-size:11.5px; text-transform:uppercase; letter-spacing:.06em; color:var(--ink-3);
}}
.meta b {{ color:var(--ink-2); font-weight:500; }}
.kind {{ font-family:"IBM Plex Mono",ui-monospace,monospace; font-size:11.5px; text-transform:uppercase;
  letter-spacing:.08em; color:var(--accent); margin:0 0 10px; }}
.arch {{
  display:flex; flex-wrap:wrap; gap:6px; margin-top:16px;
  font-family:"IBM Plex Mono",ui-monospace,monospace; font-size:11px;
}}
.arch .archlbl {{ text-transform:uppercase; letter-spacing:.06em; color:var(--ink-3);
  align-self:center; margin-right:4px; }}
.arch a {{
  color:var(--ink-2); text-decoration:none; padding:2px 7px; border-radius:6px;
  border:1px solid var(--hair); background:var(--panel);
}}
.arch a:hover {{ border-color:var(--ink-3); color:var(--ink); }}
.arch a.on {{ border-color:var(--accent); color:var(--accent); background:var(--accent-soft); }}
.arch a .ac {{ color:var(--ink-3); }}
.arch a.on .ac {{ color:var(--accent); }}
section {{ margin-top:40px; }}
h2 {{
  font-size:13px; text-transform:uppercase; letter-spacing:.09em; font-weight:600;
  color:var(--ink); margin:0 0 4px; display:flex; align-items:center; gap:10px;
}}
h2 .count {{
  font-family:"IBM Plex Mono",monospace; font-size:11px; color:var(--accent);
  border:1px solid var(--accent-soft); background:var(--accent-soft); border-radius:999px;
  padding:1px 8px; letter-spacing:.02em;
}}
.shdr {{ color:var(--ink-2); font-size:12.5px; margin:0 0 18px; }}
.shdr em {{ color:var(--ink); font-style:normal; font-weight:600; }}
.empty, .nochart {{ color:var(--ink-3); font-size:13px; font-style:italic; }}
.note {{ margin-top:26px; font-size:11.5px; color:var(--ink-3); font-style:italic; }}
.card {{
  display:grid; grid-template-columns:290px 1fr; gap:0;
  background:var(--panel); border:1px solid var(--hair); border-radius:10px;
  box-shadow:var(--shadow); overflow:hidden; margin-bottom:14px;
}}
.readout {{ padding:16px 18px; border-right:1px solid var(--hair); }}
.idline {{ display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; }}
.tkr {{ font-family:"IBM Plex Mono",monospace; font-weight:600; font-size:18px; letter-spacing:.02em; }}
.px {{ font-family:"IBM Plex Mono",monospace; font-size:14px; color:var(--ink-2); font-variant-numeric:tabular-nums; }}
.chg {{ font-family:"IBM Plex Mono",monospace; font-size:12.5px; font-weight:500; font-variant-numeric:tabular-nums; }}
.chg.up {{ color:var(--up); }}
.chg.down {{ color:var(--down); }}
.nm {{ font-size:13px; margin-top:6px; color:var(--ink); }}
.sec {{ font-size:11px; text-transform:uppercase; letter-spacing:.06em; color:var(--ink-3); margin-top:2px; }}
.metrics {{
  display:grid; grid-template-columns:1fr 1fr; gap:1px; margin-top:14px;
  background:var(--hair-2); border:1px solid var(--hair-2); border-radius:7px; overflow:hidden;
}}
.m {{ background:var(--panel-2); padding:7px 9px; display:flex; flex-direction:column; gap:1px; }}
.mk {{ font-size:9.5px; text-transform:uppercase; letter-spacing:.06em; color:var(--ink-3); }}
.mv {{ font-family:"IBM Plex Mono",monospace; font-size:13px; font-variant-numeric:tabular-nums; color:var(--ink); }}
.mv.hot {{ color:var(--vol-hot); font-weight:600; }}
.mv.at {{ color:var(--up); font-weight:600; }}
.mv.near {{ color:var(--accent); }}
.mv.far {{ color:var(--ink-2); }}
.pats {{ margin-top:12px; font-size:10.5px; color:var(--ink-3); line-height:1.45; }}
.chartwrap {{ padding:14px 16px; min-width:0; display:flex; align-items:center; }}
svg.spark {{ width:100%; height:200px; display:block; overflow:visible; }}
svg.spark .pl {{ fill:none; stroke:var(--ink); stroke-width:2; stroke-linejoin:round; stroke-linecap:round; }}
svg.spark .pe {{ fill:var(--accent); stroke:var(--panel); stroke-width:1.5; }}
svg.spark .vb {{ fill:var(--vol); }}
svg.spark .vb.hot {{ fill:var(--vol-hot); }}
svg.spark .pivot {{ stroke:var(--pivot); stroke-width:1; stroke-dasharray:3 3; opacity:.8; }}
svg.spark .pivlab, svg.spark .plab {{ font-family:"IBM Plex Mono",monospace; font-size:9px; fill:var(--ink-3); }}
svg.spark .plab {{ fill:var(--ink-2); }}
svg.spark .xt {{ font-family:"IBM Plex Mono",monospace; font-size:9px; fill:var(--ink-3); }}
footer {{ margin-top:56px; padding-top:20px; border-top:1px solid var(--hair); font-size:11.5px; color:var(--ink-3); }}
footer code {{ font-family:"IBM Plex Mono",monospace; background:var(--panel-2); padding:1px 5px; border-radius:4px; }}
@media (max-width:720px) {{
  .card {{ grid-template-columns:1fr; }}
  .readout {{ border-right:none; border-bottom:1px solid var(--hair); }}
}}
</style>

<div class="wrap">
<header>
  <p class="kind">{kind}</p>
  <h1>Volume Pivot Screen</h1>
  <p class="lede">Stocks over $20 with at least two consecutive weeks of above-average volume and a fresh
  last-day volume pop, sitting near their breakout pivot. Rebuilt each weekday around noon Central from Hawkeye's scan.</p>
  <div class="meta">
    <span><b>scan data:</b> {data_date}</span>
    <span><b>hawkeye ran:</b> {scan_updated}</span>
    <span><b>page built:</b> {generated}</span>
    <span><b>universe:</b> {universe}</span>
    <span><b>hits:</b> {n_pass} + {n_watch} watch</span>
  </div>
  {arch}
</header>
{body}
{noise}
<footer>
  Source: hawkeyecharts.com pattern API (Stage-2 Pocket Pivot, Episodic Pivot, Power Trend, 52-Week High Breakout,
  VCP + Vol Surge, Follow-Through Day), de-duplicated. Volume math is computed here from daily bars:
  weeks are 5-session blocks; a week is "elevated" when its average volume &#8805; 1.5&#215; the ~10-week baseline
  (the baseline sits before the lookback window); <code>last-day pop</code> = last session / prior 20.
  Chart shows ~70 sessions; blue bars mark days &#8805; 1.5&#215; that baseline; dashed line is the pivot.
  Educational only &#8212; not financial advice.
</footer>
</div>
"""


def main():
    """Usage: build_screen.py [OUTDIR]   (default OUTDIR = ./public)

    Writes OUTDIR/<scan-date>.html, refreshes OUTDIR/index.html (latest scan +
    the day switcher), and keeps OUTDIR/manifest.json as the archive record.
    Prior day pages already in OUTDIR are left untouched.
    """
    outdir = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else "public"
    os.makedirs(outdir, exist_ok=True)

    rows, meta = collect()
    if len(meta.get("errs", [])) >= len(PATTERNS):
        sys.stderr.write("ALL PATTERN FEEDS FAILED - leaving existing pages in place\n")
        sys.exit(3)

    scan_date = str(meta.get("data_date") or datetime.date.today().isoformat())
    t1 = sorted([r for r in rows if tier(r) == 1], key=rank_key)
    t2 = sorted([r for r in rows if tier(r) == 2], key=rank_key)

    mpath = os.path.join(outdir, "manifest.json")
    manifest = []
    if os.path.exists(mpath):
        try:
            manifest = json.load(open(mpath))
        except Exception:
            manifest = []
    manifest = [m for m in manifest if m.get("date") != scan_date]
    manifest.append({
        "date": scan_date,
        "pass": len(t1),
        "watch": len(t2),
        "built": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    })
    manifest.sort(key=lambda m: m["date"], reverse=True)
    json.dump(manifest, open(mpath, "w"), indent=1)

    day_html = render(rows, meta, manifest, scan_date, is_index=False)
    open(os.path.join(outdir, f"{scan_date}.html"), "w").write(day_html)

    idx_html = render(rows, meta, manifest, scan_date, is_index=True)
    open(os.path.join(outdir, "index.html"), "w").write(idx_html)

    sys.stderr.write(
        f"wrote {outdir}/index.html + {scan_date}.html : {len(t1)} pass, {len(t2)} watch, "
        f"{len(rows)} candidates; archive now {len(manifest)} day(s)\n"
    )


if __name__ == "__main__":
    main()
