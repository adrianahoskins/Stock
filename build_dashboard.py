"""
SEB Stock Intelligence Dashboard Builder v3
Accumulation analysis centerpiece. "Who's Buying?" panel with Known/Inferred/Blind Spots.
Verdict uses Market vs SEB-specific (no misleading sector reference).
"""

import json
import math
import statistics
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import openpyxl
import yfinance as yf

# -- Config -------------------------------------------------------------------
TICKER = "SEB"
MARKET_TICKER = "SPY"
SECTOR_TICKER = "IYK"  # Consumer Staples -- kept for decomposition chart only
LOOKBACK_DAYS = 365
CIK = "0000088121"
HDS_FILE = Path(__file__).parent / "SEB - HDS Current Q4-25.xlsx"
OUTPUT_FILE = Path(__file__).parent / "index.html"


# =============================================================================
# DATA FETCHING
# =============================================================================

def fetch_price_data():
    end = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=LOOKBACK_DAYS)
    print(f"Fetching price data {start.date()} to {end.date()}...")
    tickers = yf.download(
        [TICKER, MARKET_TICKER, SECTOR_TICKER],
        start=start, end=end, auto_adjust=True, progress=False
    )
    return tickers


def fetch_edgar_filings():
    print("Fetching EDGAR filings...")
    url = f"https://data.sec.gov/submissions/CIK{CIK}.json"
    req = urllib.request.Request(url, headers={
        "User-Agent": "StockIntelligence adriana.hoskins@outlook.com"
    })
    try:
        resp = urllib.request.urlopen(req)
        data = json.loads(resp.read())
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        descs = recent.get("primaryDocDescription", [])
        accessions = recent.get("accessionNumber", [])
        docs = recent.get("primaryDocument", [])

        filings = []
        target_forms = {"SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A", "4", "4/A", "13F-HR", "13F-HR/A"}
        for i in range(len(forms)):
            if forms[i] in target_forms:
                acc_clean = accessions[i].replace("-", "") if i < len(accessions) else ""
                doc_name = docs[i] if i < len(docs) else ""
                filings.append({
                    "date": dates[i] if i < len(dates) else "",
                    "form": forms[i],
                    "description": descs[i] if i < len(descs) else "",
                    "url": f"https://www.sec.gov/Archives/edgar/data/{CIK.lstrip('0')}/{acc_clean}/{doc_name}" if acc_clean and doc_name else "",
                })
        print(f"  Found {len(filings)} relevant filings (13D/G, Form 4)")
        return filings
    except Exception as e:
        print(f"  EDGAR fetch failed: {e}")
        return []


def load_hds_data():
    if not HDS_FILE.exists():
        print("  HDS file not found, skipping holder analysis")
        return {}
    print(f"Loading HDS data from {HDS_FILE.name}...")
    wb = openpyxl.load_workbook(HDS_FILE, data_only=True)
    ws = wb["HDS"]
    holders = {}
    for row in ws.iter_rows(min_row=2, max_col=13, values_only=True):
        qtr, _, name, position, change, filing_date, source, pct_out, pct_port, insider, inst_type, metro, country = row
        if not name or name == "Holder Name" or qtr == "Qtr":
            continue
        if name not in holders:
            holders[name] = {}
        holders[name][qtr] = {
            "position": int(position) if position else 0,
            "change": int(change) if change else 0,
            "pct_out": float(pct_out) if pct_out else 0,
            "type": inst_type or "",
            "metro": metro or "",
        }
    print(f"  Loaded {len(holders)} unique holders")
    return holders


# =============================================================================
# ANALYSIS ENGINE
# =============================================================================

def build_daily_records(tickers):
    close = tickers["Close"]
    high = tickers["High"]
    low = tickers["Low"]
    volume = tickers["Volume"]
    records = []
    dates = close.index.tolist()

    for i in range(1, len(dates)):
        date = dates[i]
        prev = dates[i - 1]
        seb_c = close.loc[date, TICKER]
        seb_p = close.loc[prev, TICKER]
        seb_h = high.loc[date, TICKER]
        seb_l = low.loc[date, TICKER]
        spy_c = close.loc[date, MARKET_TICKER]
        spy_p = close.loc[prev, MARKET_TICKER]
        iyk_c = close.loc[date, SECTOR_TICKER]
        iyk_p = close.loc[prev, SECTOR_TICKER]
        seb_v = volume.loc[date, TICKER]

        vals = [seb_c, seb_p, seb_h, seb_l, spy_c, spy_p, iyk_c, iyk_p, seb_v]
        if any(v != v for v in vals) or any(v is None for v in vals):
            continue

        seb_ret = (seb_c - seb_p) / seb_p
        spy_ret = (spy_c - spy_p) / spy_p
        iyk_ret = (iyk_c - iyk_p) / iyk_p

        market_comp = spy_ret
        seb_specific = seb_ret - spy_ret
        sector_comp = iyk_ret - spy_ret
        company_comp = seb_ret - iyk_ret

        hl_range = float(seb_h - seb_l)
        ad_mult = ((float(seb_c) - float(seb_l)) - (float(seb_h) - float(seb_c))) / hl_range if hl_range > 0 else 0.0
        ad_value = ad_mult * float(seb_v)

        records.append({
            "date": date.strftime("%Y-%m-%d"),
            "close": round(float(seb_c), 2),
            "high": round(float(seb_h), 2),
            "low": round(float(seb_l), 2),
            "ret": round(float(seb_ret) * 100, 2),
            "spy_ret": round(float(spy_ret) * 100, 2),
            "iyk_ret": round(float(iyk_ret) * 100, 2),
            "volume": int(seb_v),
            "market_pct": round(float(market_comp) * 100, 2),
            "seb_specific_pct": round(float(seb_specific) * 100, 2),
            "sector_pct": round(float(sector_comp) * 100, 2),
            "company_pct": round(float(company_comp) * 100, 2),
            "ad_value": round(ad_value, 0),
        })
    return records


def compute_accumulation(records):
    obv = 0
    cum_ad = 0
    for r in records:
        if r["ret"] > 0:
            obv += r["volume"]
        elif r["ret"] < 0:
            obv -= r["volume"]
        r["obv"] = obv
        cum_ad += r["ad_value"]
        r["cum_ad"] = round(cum_ad, 0)

    for i, r in enumerate(records):
        if i >= 20:
            r["vol_avg_20"] = int(statistics.mean([records[j]["volume"] for j in range(i - 20, i)]))
            r["vol_ratio"] = round(r["volume"] / r["vol_avg_20"], 2) if r["vol_avg_20"] > 0 else 1.0
        else:
            r["vol_avg_20"] = r["volume"]
            r["vol_ratio"] = 1.0

    if len(records) >= 120:
        early_vol = statistics.mean([r["volume"] for r in records[:60]])
        late_vol = statistics.mean([r["volume"] for r in records[-60:]])
        vol_regime_shift = late_vol / early_vol if early_vol > 0 else 1.0
    else:
        vol_regime_shift = 1.0

    for i, r in enumerate(records):
        if i < 20:
            r["accum_score"] = 50
            r["_factors"] = {"obv": False, "ad": False, "pv": 12, "vol": 0}
            continue

        window = records[i - 20:i + 1]

        obv_up = window[-1]["obv"] > window[0]["obv"]
        obv_score = 25 if obv_up else 0

        ad_up = window[-1]["cum_ad"] > window[0]["cum_ad"]
        ad_score = 25 if ad_up else 0

        up_vol = sum(w["volume"] for w in window if w["ret"] > 0)
        dn_vol = sum(w["volume"] for w in window if w["ret"] < 0)
        total = up_vol + dn_vol
        pv_ratio = up_vol / total if total > 0 else 0.5
        pv_score = min(25, int(pv_ratio * 50))

        current_avg = statistics.mean([w["volume"] for w in window])
        baseline_avg = statistics.mean([records[j]["volume"] for j in range(max(0, i - 60), i - 20)]) if i >= 60 else current_avg
        vol_elev = current_avg / baseline_avg if baseline_avg > 0 else 1.0
        vol_score = min(25, int((vol_elev - 1) * 25)) if vol_elev > 1 else 0

        r["accum_score"] = min(100, max(0, obv_score + ad_score + pv_score + vol_score))
        r["_factors"] = {"obv": obv_up, "ad": ad_up, "pv": pv_score, "vol": vol_score}

    return vol_regime_shift


def compute_buying_profile(records):
    """Analyze accumulation pattern to infer buyer characteristics."""
    if len(records) < 60:
        return {}

    # Find the regime change point: first day where 20d avg > 2x the first-60-day baseline
    baseline = statistics.mean([r["volume"] for r in records[:60]])
    regime_start = None
    for i in range(60, len(records)):
        if records[i].get("vol_avg_20", 0) > baseline * 2:
            regime_start = records[i]["date"]
            break

    if not regime_start:
        regime_start = records[-60]["date"]

    # Analyze the accumulation period (from regime start to now)
    accum_records = [r for r in records if r["date"] >= regime_start]
    if not accum_records:
        accum_records = records[-60:]

    # 1. Buying persistence: what % of days had above-average volume?
    avg_vol_accum = statistics.mean([r["volume"] for r in accum_records])
    above_avg_days = sum(1 for r in accum_records if r["volume"] > avg_vol_accum)
    persistence_pct = round(above_avg_days / len(accum_records) * 100)

    # 2. Up-volume asymmetry: ratio of volume on up-days vs down-days
    up_vol = sum(r["volume"] for r in accum_records if r["ret"] > 0)
    dn_vol = sum(r["volume"] for r in accum_records if r["ret"] < 0)
    total_vol = up_vol + dn_vol
    up_vol_pct = round(up_vol / total_vol * 100) if total_vol > 0 else 50

    # 3. Volume consistency: coefficient of variation (lower = more systematic)
    vols = [r["volume"] for r in accum_records]
    vol_std = statistics.stdev(vols) if len(vols) > 1 else 0
    vol_cv = round(vol_std / avg_vol_accum * 100) if avg_vol_accum > 0 else 0

    # 4. Price impact: did price rise steadily or in bursts?
    first_price = accum_records[0]["close"]
    last_price = accum_records[-1]["close"]
    price_change_pct = round((last_price - first_price) / first_price * 100, 1)

    # 5. Estimated shares accumulated (net up-volume minus net down-volume as proxy)
    est_net_shares = sum(r["volume"] for r in accum_records if r["ret"] > 0) - sum(r["volume"] for r in accum_records if r["ret"] < 0)

    # 6. Daily volume range during accumulation
    vol_min = min(r["volume"] for r in accum_records)
    vol_max = max(r["volume"] for r in accum_records)
    vol_median = sorted(r["volume"] for r in accum_records)[len(accum_records) // 2]

    # Determine buyer profile
    if vol_cv < 80 and persistence_pct > 40 and up_vol_pct > 55:
        buyer_type = "Institutional (Algorithmic)"
        buyer_desc = "Consistent volume elevation with systematic buying on up-days. Pattern is characteristic of algorithmic accumulation by one or more institutional buyers using VWAP or TWAP execution strategies."
    elif vol_cv < 100 and up_vol_pct > 52:
        buyer_type = "Institutional (Active)"
        buyer_desc = "Elevated volume with moderate consistency. Pattern suggests active institutional buying, possibly multiple buyers with overlapping timelines."
    elif vol_cv >= 100 and persistence_pct < 35:
        buyer_type = "Mixed / Retail Momentum"
        buyer_desc = "Sporadic volume spikes with inconsistent pattern. Could indicate retail momentum buying or event-driven institutional activity."
    else:
        buyer_type = "Undetermined"
        buyer_desc = "Volume pattern does not clearly match known institutional or retail signatures. Multiple concurrent buyers with different strategies could produce this pattern."

    return {
        "regime_start": regime_start,
        "trading_days": len(accum_records),
        "persistence_pct": persistence_pct,
        "up_vol_pct": up_vol_pct,
        "vol_cv": vol_cv,
        "price_change_pct": price_change_pct,
        "first_price": first_price,
        "last_price": last_price,
        "est_net_shares": est_net_shares,
        "vol_min": vol_min,
        "vol_max": vol_max,
        "vol_median": vol_median,
        "avg_vol": int(avg_vol_accum),
        "buyer_type": buyer_type,
        "buyer_desc": buyer_desc,
    }


def compute_anomalies(records):
    anomalies = []
    for i, r in enumerate(records):
        flags = []
        if i >= 20 and r["vol_ratio"] >= 2.0:
            flags.append({
                "type": "volume_spike",
                "severity": "high" if r["vol_ratio"] > 3 else "medium",
                "msg": f"Volume {r['volume']:,} was {r['vol_ratio']:.1f}x the 20-day avg ({r['vol_avg_20']:,})"
            })
        if abs(r["seb_specific_pct"]) > 2:
            d = "outperformed" if r["seb_specific_pct"] > 0 else "underperformed"
            flags.append({
                "type": "divergence",
                "severity": "high" if abs(r["seb_specific_pct"]) > 4 else "medium",
                "msg": f"SEB {d} the broad market by {abs(r['seb_specific_pct']):.1f}%"
            })
        if abs(r["ret"]) > 3:
            d = "gained" if r["ret"] > 0 else "dropped"
            flags.append({
                "type": "large_move",
                "severity": "high" if abs(r["ret"]) > 5 else "medium",
                "msg": f"SEB {d} {abs(r['ret']):.1f}% in a single session"
            })
        if r.get("accum_score", 50) >= 75:
            flags.append({
                "type": "accumulation",
                "severity": "high" if r["accum_score"] >= 90 else "medium",
                "msg": f"Accumulation score hit {r['accum_score']}/100 -- sustained buying pressure"
            })
        if flags:
            anomalies.append({"date": r["date"], "flags": flags})
    return anomalies


def build_holder_changes(holders):
    changes = []
    for name, qtrs in holders.items():
        q4 = qtrs.get("Q4", qtrs.get("Qtr", None))
        q3 = qtrs.get("Q3", None)
        if q4:
            pos = q4["position"]
            chg = q4["change"]
            pct = q4["pct_out"]
            inst_type = q4["type"]
            total_chg = (pos - q3["position"]) if q3 else chg
            changes.append({"name": name, "position": pos, "change": total_chg, "pct_out": pct, "type": inst_type})
    changes.sort(key=lambda x: abs(x["change"]), reverse=True)
    return changes


def generate_verdict(r):
    """Plain-English verdict. Uses Market vs SEB-specific only -- no sector."""
    ret = r["ret"]
    d = "rose" if ret > 0 else "fell" if ret < 0 else "was flat"
    mkt = r["market_pct"]
    seb_spec = r["seb_specific_pct"]

    v = f"SEB {d} {abs(ret):.1f}%"

    if abs(ret) < 0.3:
        v += ". Essentially flat."
    elif abs(mkt) > abs(seb_spec):
        v += f", driven primarily by market-wide forces."
    else:
        v += f", driven primarily by SEB-specific factors."

    if abs(mkt) > 0.5:
        md = "up" if mkt > 0 else "down"
        v += f" The broad market (S&P 500) was {md} {abs(mkt):.1f}%."

    if abs(seb_spec) > 1.0:
        sd = "outperformed" if seb_spec > 0 else "underperformed"
        v += f" SEB {sd} the market by {abs(seb_spec):.1f}% -- company-specific signal."

    score = r.get("accum_score", 50)
    if score >= 75:
        v += f" Accumulation score elevated at {score}/100."

    return v


# =============================================================================
# HTML GENERATION
# =============================================================================

def generate_html(records, anomalies, vol_regime_shift, holder_changes, edgar_filings, buying_profile):
    latest = records[-1]
    verdict = generate_verdict(latest)
    recent_verdicts = [{"date": r["date"], "verdict": generate_verdict(r), "ret": r["ret"]} for r in records[-30:]]

    cutoff_90 = records[-90]["date"] if len(records) >= 90 else records[0]["date"]
    recent_anomalies = [a for a in anomalies if a["date"] >= cutoff_90]

    rets = [r["ret"] for r in records]
    stats = {
        "avg_ret": round(statistics.mean(rets), 3),
        "vol": round(statistics.stdev(rets), 2),
        "avg_volume": int(statistics.mean([r["volume"] for r in records])),
        "days": len(records),
        "up": sum(1 for r in rets if r > 0),
        "down": sum(1 for r in rets if r < 0),
        "regime_shift": round(vol_regime_shift, 1),
        "latest_score": latest.get("accum_score", 50),
        "anomaly_count": sum(len(a["flags"]) for a in recent_anomalies),
    }

    top_buyers = [h for h in holder_changes if h["change"] > 0][:15]
    top_sellers = [h for h in holder_changes if h["change"] < 0][:15]

    # -- Build HTML fragments --
    buyers_html = "".join(f'<tr><td>{h["name"]}</td><td class="num">{h["position"]:,}</td><td class="num up">+{h["change"]:,}</td><td class="num">{h["pct_out"]:.2f}%</td><td>{h["type"]}</td></tr>' for h in top_buyers)
    sellers_html = "".join(f'<tr><td>{h["name"]}</td><td class="num">{h["position"]:,}</td><td class="num down">{h["change"]:,}</td><td class="num">{h["pct_out"]:.2f}%</td><td>{h["type"]}</td></tr>' for h in top_sellers)

    filings_html = ""
    for f in edgar_filings[:20]:
        link = f'<a href="{f["url"]}" target="_blank">{f["form"]}</a>' if f["url"] else f["form"]
        filings_html += f'<tr><td>{f["date"]}</td><td>{link}</td><td>{f["description"]}</td></tr>'

    anomaly_html = "".join(
        f'<div class="anomaly-item {fl["severity"]}"><span class="anomaly-date">{a["date"]}</span><span class="anomaly-badge {fl["severity"]}">{fl["type"].replace("_"," ")}</span><span class="anomaly-msg">{fl["msg"]}</span></div>'
        for a in reversed(recent_anomalies) for fl in a["flags"]
    )

    verdict_hist_html = "".join(
        f'<div class="vh-item"><span class="vh-date">{v["date"]}</span><span class="vh-ret {"up" if v["ret"]>0 else "down" if v["ret"]<0 else "flat"}">{"+" if v["ret"]>0 else ""}{v["ret"]:.1f}%</span><span class="vh-text">{v["verdict"]}</span></div>'
        for v in reversed(recent_verdicts)
    )

    score = stats["latest_score"]
    score_cls = "up" if score >= 70 else "down" if score <= 30 else "flat"
    score_label = "Strong Accumulation" if score >= 75 else "Moderate" if score >= 50 else "Neutral/Distribution"
    regime_cls = "up" if stats["regime_shift"] >= 2 else "flat"
    latest_cls = "up" if latest["ret"] > 0 else "down" if latest["ret"] < 0 else "flat"
    latest_sign = "+" if latest["ret"] > 0 else ""
    factors = latest.get("_factors", {"obv": False, "ad": False, "pv": 0, "vol": 0})

    # Buying profile
    bp = buying_profile
    bp_regime_start = bp.get("regime_start", "N/A")
    bp_trading_days = bp.get("trading_days", 0)
    bp_buyer_type = bp.get("buyer_type", "Unknown")
    bp_buyer_desc = bp.get("buyer_desc", "")
    bp_persistence = bp.get("persistence_pct", 0)
    bp_up_vol = bp.get("up_vol_pct", 50)
    bp_vol_cv = bp.get("vol_cv", 0)
    bp_price_chg = bp.get("price_change_pct", 0)
    bp_first = bp.get("first_price", 0)
    bp_last = bp.get("last_price", 0)
    bp_est_shares = bp.get("est_net_shares", 0)
    bp_vol_min = bp.get("vol_min", 0)
    bp_vol_max = bp.get("vol_max", 0)
    bp_vol_med = bp.get("vol_median", 0)
    bp_avg_vol = bp.get("avg_vol", 0)

    # Accumulation profile narrative
    accum_narrative = f'Pattern began around {bp_regime_start} with daily volume shifting from a pre-period baseline to a sustained {bp_avg_vol:,} shares/day (median {bp_vol_med:,}, range {bp_vol_min:,}-{bp_vol_max:,}). '
    accum_narrative += f'Over {bp_trading_days} trading days, price moved from ${bp_first:,.2f} to ${bp_last:,.2f} ({bp_price_chg:+.1f}%). '
    accum_narrative += f'{bp_up_vol}% of total volume occurred on up-days (above 55% = buying bias). '
    accum_narrative += f'Volume consistency CV is {bp_vol_cv}% (below 80% = systematic, above 100% = sporadic). '
    accum_narrative += f'Net estimated share accumulation (up-volume minus down-volume proxy): {bp_est_shares:,} shares.'

    # Buyer type styling
    buyer_type_cls = "up" if "Institutional" in bp_buyer_type else "amber" if bp_buyer_type == "Undetermined" else "flat"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SEB Stock Intelligence Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0/dist/chartjs-adapter-date-fns.bundle.min.js"></script>
<style>
:root {{
  --bg:#0a0e17; --surface:#111827; --surface-2:#1a2332; --border:#1e2d3d;
  --text:#e2e8f0; --text-muted:#8896a8; --accent:#3b82f6;
  --green:#22c55e; --red:#ef4444; --amber:#f59e0b;
  --market-color:#6366f1; --company-color:#3b82f6;
}}
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--text);line-height:1.6;}}

.header{{background:linear-gradient(135deg,#111827 0%,#1a1f35 100%);border-bottom:1px solid var(--border);padding:24px 32px;display:flex;justify-content:space-between;align-items:center;}}
.header h1{{font-size:20px;font-weight:600;letter-spacing:-0.3px;}}
.header h1 span{{color:var(--accent);}}
.header-meta{{font-size:13px;color:var(--text-muted);}}
.source-badge{{display:inline-flex;align-items:center;gap:6px;font-size:11px;color:var(--text-muted);background:var(--surface-2);padding:4px 10px;border-radius:20px;}}
.source-badge .dot{{width:6px;height:6px;border-radius:50%;background:var(--green);}}

.container{{max-width:1440px;margin:0 auto;padding:24px;display:grid;gap:20px;}}
.card{{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:24px;}}
.card-title{{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1.2px;color:var(--text-muted);margin-bottom:16px;}}
.row-2{{display:grid;grid-template-columns:1fr 1fr;gap:20px;}}
.row-3{{display:grid;grid-template-columns:2fr 1fr;gap:20px;}}

.up{{color:var(--green);}} .down{{color:var(--red);}} .flat{{color:var(--text-muted);}} .amber{{color:var(--amber);}}

/* Verdict */
.verdict-card{{background:linear-gradient(135deg,#111827 0%,#162033 100%);border-left:4px solid var(--accent);}}
.verdict-date{{font-size:13px;color:var(--text-muted);margin-bottom:8px;}}
.verdict-text{{font-size:18px;font-weight:500;line-height:1.5;}}
.verdict-metrics{{display:flex;gap:24px;margin-top:16px;padding-top:16px;border-top:1px solid var(--border);flex-wrap:wrap;}}
.metric{{display:flex;flex-direction:column;}}
.metric-label{{font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.8px;}}
.metric-value{{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums;}}

/* Accumulation Hero */
.accum-hero{{background:linear-gradient(135deg,#111827 0%,#1a2332 100%);border:2px solid var(--amber);}}
.accum-hero.high{{border-color:var(--red);}}
.accum-score-ring{{display:flex;align-items:center;gap:24px;}}
.score-circle{{width:120px;height:120px;border-radius:50%;display:flex;align-items:center;justify-content:center;flex-direction:column;border:4px solid;flex-shrink:0;}}
.score-circle .score-num{{font-size:36px;font-weight:800;}}
.score-circle .score-max{{font-size:12px;color:var(--text-muted);}}
.accum-details{{flex:1;}}
.accum-details h3{{font-size:16px;font-weight:600;margin-bottom:8px;}}
.accum-details p{{font-size:13px;color:var(--text-muted);line-height:1.5;}}
.factor-bar{{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap;}}
.factor-bar .factor{{background:var(--surface-2);padding:6px 12px;border-radius:6px;font-size:11px;font-weight:600;}}
.factor-bar .factor.active{{background:var(--green);color:#000;}}
.factor-bar .factor.inactive{{opacity:0.4;}}

/* Who's Buying */
.whos-buying{{border:2px solid var(--accent);background:linear-gradient(135deg,#111827 0%,#131d2e 100%);}}
.wb-section{{margin-bottom:20px;}}
.wb-section:last-child{{margin-bottom:0;}}
.wb-section-title{{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:1px;margin-bottom:10px;display:flex;align-items:center;gap:8px;}}
.wb-badge{{font-size:9px;padding:2px 8px;border-radius:4px;font-weight:700;letter-spacing:0.5px;}}
.wb-badge.known{{background:rgba(34,197,94,0.15);color:var(--green);}}
.wb-badge.inferred{{background:rgba(59,130,246,0.15);color:var(--accent);}}
.wb-badge.blind{{background:rgba(239,68,68,0.15);color:var(--red);}}
.wb-narrative{{font-size:13px;color:var(--text-muted);line-height:1.6;}}
.wb-profile-type{{font-size:16px;font-weight:700;margin-bottom:6px;}}
.wb-stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin-top:12px;}}
.wb-stat{{background:var(--surface-2);border-radius:6px;padding:10px;text-align:center;}}
.wb-stat .metric-value{{font-size:16px;}}
.blind-spots-list{{list-style:none;padding:0;}}
.blind-spots-list li{{padding:6px 0;font-size:13px;color:var(--text-muted);border-bottom:1px solid rgba(30,45,61,0.3);display:flex;gap:8px;align-items:baseline;}}
.blind-spots-list li:last-child{{border-bottom:none;}}
.blind-spots-list .bs-icon{{color:var(--red);font-weight:700;flex-shrink:0;}}
.blind-spots-list .bs-fix{{color:var(--amber);font-size:11px;font-style:italic;}}

/* Charts */
.chart-container{{position:relative;height:300px;}}
.chart-container.tall{{height:400px;}}

/* Stats */
.stats-bar{{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px;}}
.stat-box{{background:var(--surface-2);border-radius:8px;padding:14px;text-align:center;}}
.stat-box .metric-value{{font-size:18px;}}

/* Table */
table{{width:100%;border-collapse:collapse;font-size:12px;}}
th{{text-align:left;color:var(--text-muted);font-size:10px;text-transform:uppercase;letter-spacing:0.8px;padding:8px 10px;border-bottom:1px solid var(--border);}}
td{{padding:7px 10px;border-bottom:1px solid rgba(30,45,61,0.4);}}
td.num{{text-align:right;font-variant-numeric:tabular-nums;font-weight:600;}}
tbody tr:hover{{background:var(--surface-2);}}
a{{color:var(--accent);text-decoration:none;}}
a:hover{{text-decoration:underline;}}
.table-scroll{{max-height:400px;overflow-y:auto;}}

/* Anomalies */
.anomaly-list{{display:flex;flex-direction:column;gap:8px;max-height:500px;overflow-y:auto;}}
.anomaly-item{{display:flex;align-items:flex-start;gap:10px;padding:10px 12px;background:var(--surface-2);border-radius:8px;border-left:3px solid var(--amber);}}
.anomaly-item.high{{border-left-color:var(--red);}}
.anomaly-date{{font-size:12px;color:var(--text-muted);white-space:nowrap;min-width:78px;}}
.anomaly-badge{{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;padding:2px 8px;border-radius:4px;background:rgba(245,158,11,0.15);color:var(--amber);white-space:nowrap;}}
.anomaly-badge.high{{background:rgba(239,68,68,0.15);color:var(--red);}}
.anomaly-msg{{font-size:13px;flex:1;}}

/* Verdict history */
.verdict-history{{display:flex;flex-direction:column;gap:6px;max-height:500px;overflow-y:auto;}}
.vh-item{{display:flex;gap:14px;padding:8px 12px;border-radius:8px;background:var(--surface-2);align-items:baseline;}}
.vh-date{{font-size:12px;color:var(--text-muted);white-space:nowrap;min-width:78px;}}
.vh-ret{{font-size:13px;font-weight:700;font-variant-numeric:tabular-nums;min-width:55px;text-align:right;}}
.vh-text{{font-size:12px;color:var(--text);flex:1;}}

/* Breakdown bars */
.bar-row{{display:flex;align-items:center;gap:10px;margin-bottom:8px;}}
.bar-label{{width:72px;font-size:12px;color:var(--text-muted);text-align:right;}}
.bar-track{{flex:1;height:26px;background:var(--surface-2);border-radius:6px;overflow:hidden;}}
.bar-fill{{height:100%;border-radius:6px;display:flex;align-items:center;padding:0 8px;font-size:11px;font-weight:600;min-width:fit-content;}}
.bar-fill.market{{background:var(--market-color);}}
.bar-fill.company{{background:var(--company-color);}}

/* Conglomerate note */
.note-box{{background:var(--surface-2);border:1px solid var(--border);border-radius:8px;padding:12px 16px;font-size:12px;color:var(--text-muted);line-height:1.5;margin-top:12px;}}
.note-box strong{{color:var(--amber);}}

/* PDF button */
.pdf-btn{{background:var(--accent);color:#fff;border:none;padding:8px 20px;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;display:flex;align-items:center;gap:8px;}}
.pdf-btn:hover{{background:#2563eb;}}
.pdf-btn svg{{width:16px;height:16px;fill:currentColor;}}

@media(max-width:1000px){{.row-2,.row-3{{grid-template-columns:1fr;}}}}
::-webkit-scrollbar{{width:6px;}}::-webkit-scrollbar-track{{background:var(--surface);}}::-webkit-scrollbar-thumb{{background:var(--border);border-radius:3px;}}

/* Print styles */
@media print {{
  @page {{ size:A3 portrait; margin:0.5in; }}
  body {{ background:#fff !important; color:#111 !important; font-size:10px !important; -webkit-print-color-adjust:exact; print-color-adjust:exact; }}
  .header {{ background:#f8f9fa !important; border-bottom:2px solid #333 !important; padding:16px 24px !important; }}
  .header h1 {{ color:#111 !important; }} .header h1 span {{ color:#2563eb !important; }}
  .header-meta {{ color:#666 !important; }}
  .source-badge {{ background:#eee !important; color:#666 !important; }}
  .source-badge .dot {{ background:#22c55e !important; }}
  .container {{ padding:12px !important; gap:14px !important; max-width:100% !important; }}
  .card {{ background:#fff !important; border:1px solid #ddd !important; padding:16px !important; page-break-inside:avoid; }}
  .card-title {{ color:#666 !important; }}
  .verdict-card {{ background:#f0f4ff !important; border-left:4px solid #2563eb !important; }}
  .verdict-text {{ font-size:14px !important; color:#111 !important; }}
  .accum-hero {{ border:2px solid #f59e0b !important; background:#fffbeb !important; }}
  .accum-hero.high {{ border-color:#ef4444 !important; }}
  .whos-buying {{ border:2px solid #2563eb !important; background:#f0f7ff !important; }}
  .up {{ color:#16a34a !important; }} .down {{ color:#dc2626 !important; }} .flat {{ color:#666 !important; }} .amber {{ color:#d97706 !important; }}
  .metric-value {{ color:#111 !important; }}
  .metric-value.up {{ color:#16a34a !important; }} .metric-value.down {{ color:#dc2626 !important; }}
  .stat-box, .wb-stat {{ background:#f3f4f6 !important; }}
  .score-circle {{ border-color:#d97706 !important; }}
  .score-num {{ color:#111 !important; }}
  .factor {{ background:#e5e7eb !important; color:#111 !important; }}
  .factor.active {{ background:#16a34a !important; color:#fff !important; }}
  .factor.inactive {{ opacity:0.3 !important; }}
  .surface-2, .anomaly-item, .vh-item {{ background:#f9fafb !important; }}
  .anomaly-item {{ border-left:3px solid #d97706 !important; }}
  .anomaly-item.high {{ border-left-color:#dc2626 !important; }}
  .anomaly-badge {{ background:#fef3c7 !important; color:#92400e !important; }}
  .anomaly-badge.high {{ background:#fee2e2 !important; color:#991b1b !important; }}
  .wb-badge.known {{ background:#dcfce7 !important; color:#166534 !important; }}
  .wb-badge.inferred {{ background:#dbeafe !important; color:#1e40af !important; }}
  .wb-badge.blind {{ background:#fee2e2 !important; color:#991b1b !important; }}
  .wb-narrative {{ color:#444 !important; }}
  .wb-profile-type {{ color:#111 !important; }}
  .blind-spots-list li {{ color:#444 !important; border-bottom:1px solid #e5e7eb !important; }}
  .bs-icon {{ color:#dc2626 !important; }}
  .bs-fix {{ color:#92400e !important; }}
  .note-box {{ background:#fffbeb !important; border:1px solid #fcd34d !important; color:#444 !important; }}
  .note-box strong {{ color:#92400e !important; }}
  .bar-track {{ background:#e5e7eb !important; }}
  .bar-fill.market {{ background:#6366f1 !important; color:#fff !important; }}
  .bar-fill.company {{ background:#3b82f6 !important; color:#fff !important; }}
  .bar-label {{ color:#666 !important; }}
  table {{ font-size:10px !important; }}
  th {{ color:#666 !important; border-bottom:1px solid #ccc !important; }}
  td {{ border-bottom:1px solid #eee !important; }}
  a {{ color:#2563eb !important; }}
  .table-scroll, .anomaly-list, .verdict-history {{ max-height:none !important; overflow:visible !important; }}
  canvas {{ display:none !important; }}
  .chart-img {{ display:block !important; width:100% !important; max-height:350px !important; object-fit:contain; }}
  .chart-container {{ height:auto !important; }}
  .pdf-btn {{ display:none !important; }}
  .row-2 {{ grid-template-columns:1fr 1fr !important; }}
}}
</style>
</head>
<body>

<div class="header">
  <h1><span>SEB</span> Stock Intelligence</h1>
  <div style="display:flex;align-items:center;gap:16px;">
    <span class="source-badge"><span class="dot"></span>Yahoo Finance + SEC EDGAR</span>
    <span class="header-meta">Seaboard Corporation &middot; {stats['days']} trading days &middot; Updated {latest['date']}</span>
    <button class="pdf-btn" onclick="downloadPDF()">
      <svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zm-1 1.5L18.5 9H13V3.5zM6 20V4h5v7h7v9H6zm3-7h6v1.5H9V13zm0 3h6v1.5H9V16zm0-6h3v1.5H9V10z"/></svg>
      Download PDF
    </button>
  </div>
</div>

<div class="container">

  <!-- ROW 1: Verdict -->
  <div class="card verdict-card">
    <div class="card-title">Daily Verdict</div>
    <div class="verdict-date">{latest['date']}</div>
    <div class="verdict-text">{verdict}</div>
    <div class="verdict-metrics">
      <div class="metric"><span class="metric-label">Close</span><span class="metric-value">${latest['close']:,.2f}</span></div>
      <div class="metric"><span class="metric-label">Return</span><span class="metric-value {latest_cls}">{latest_sign}{latest['ret']:.2f}%</span></div>
      <div class="metric"><span class="metric-label">Volume</span><span class="metric-value">{latest['volume']:,}</span></div>
      <div class="metric"><span class="metric-label">20d Avg Vol</span><span class="metric-value">{latest.get('vol_avg_20',0):,}</span></div>
      <div class="metric"><span class="metric-label">Accum Score</span><span class="metric-value {score_cls}">{score}/100</span></div>
    </div>
  </div>

  <!-- ROW 2: Accumulation Hero + Volume Regime -->
  <div class="row-2">
    <div class="card accum-hero {'high' if score >= 75 else ''}">
      <div class="card-title">Accumulation Intelligence</div>
      <div class="accum-score-ring">
        <div class="score-circle" style="border-color:{'var(--red)' if score >= 75 else 'var(--amber)' if score >= 50 else 'var(--text-muted)'}">
          <span class="score-num {score_cls}">{score}</span>
          <span class="score-max">/ 100</span>
        </div>
        <div class="accum-details">
          <h3>{score_label}</h3>
          <p>Composite signal from OBV trend, A/D line, price-volume correlation, and volume regime elevation over 20-day window.</p>
          <div class="factor-bar">
            <span class="factor {'active' if factors['obv'] else 'inactive'}">OBV Trend</span>
            <span class="factor {'active' if factors['ad'] else 'inactive'}">A/D Line</span>
            <span class="factor {'active' if factors['pv'] >= 15 else 'inactive'}">Price-Vol</span>
            <span class="factor {'active' if factors['vol'] >= 10 else 'inactive'}">Vol Regime</span>
          </div>
        </div>
      </div>
    </div>
    <div class="card">
      <div class="card-title">Volume Regime Analysis</div>
      <div class="stats-bar" style="margin-bottom:16px;">
        <div class="stat-box"><div class="metric-label">Regime Shift</div><div class="metric-value {regime_cls}">{stats['regime_shift']}x</div></div>
        <div class="stat-box"><div class="metric-label">Avg Volume</div><div class="metric-value">{stats['avg_volume']:,}</div></div>
      </div>
      <p style="font-size:12px;color:var(--text-muted);margin-bottom:12px;">Compares avg daily volume of last 60 trading days to first 60 trading days. A shift &gt; 2x indicates a structural change consistent with accumulation.</p>
      <div class="chart-container" style="height:200px;"><canvas id="volumeRegimeChart"></canvas></div>
    </div>
  </div>

  <!-- ROW 3: WHO'S BUYING? -->
  <div class="card whos-buying">
    <div class="card-title">Who's Buying?</div>

    <div class="wb-section">
      <div class="wb-section-title"><span class="wb-badge inferred">INFERRED FROM DAILY DATA</span> Accumulation Profile</div>
      <div class="wb-profile-type {buyer_type_cls}">{bp_buyer_type}</div>
      <p class="wb-narrative">{bp_buyer_desc}</p>
      <p class="wb-narrative" style="margin-top:10px;">{accum_narrative}</p>
      <div class="wb-stats">
        <div class="wb-stat"><div class="metric-label">Since</div><div class="metric-value">{bp_regime_start}</div></div>
        <div class="wb-stat"><div class="metric-label">Trading Days</div><div class="metric-value">{bp_trading_days}</div></div>
        <div class="wb-stat"><div class="metric-label">Price Move</div><div class="metric-value {'up' if bp_price_chg > 0 else 'down'}">{bp_price_chg:+.1f}%</div></div>
        <div class="wb-stat"><div class="metric-label">Up-Vol Ratio</div><div class="metric-value {'up' if bp_up_vol > 55 else 'flat'}">{bp_up_vol}%</div></div>
        <div class="wb-stat"><div class="metric-label">Persistence</div><div class="metric-value">{bp_persistence}%</div></div>
        <div class="wb-stat"><div class="metric-label">Vol Consistency</div><div class="metric-value">CV {bp_vol_cv}%</div></div>
        <div class="wb-stat"><div class="metric-label">Est Net Shares</div><div class="metric-value">{bp_est_shares:,}</div></div>
        <div class="wb-stat"><div class="metric-label">Avg Daily Vol</div><div class="metric-value">{bp_avg_vol:,}</div></div>
      </div>
    </div>

    <div class="wb-section">
      <div class="wb-section-title"><span class="wb-badge known">KNOWN / FILED</span> Last Disclosed Positions (Q4 2025 13F)</div>
      <p class="wb-narrative" style="margin-bottom:10px;">Institutional holders with &gt;$100M AUM are required to file 13F quarterly (45-day delay). This data reflects positions as of Dec 31, 2025. The accumulation pattern began around {bp_regime_start} -- current positions are unknown until Q1 2026 13F filings are published.</p>
      <p class="wb-narrative" style="font-size:11px;color:var(--amber);">Q4 2025: {len(top_buyers)} net buyers, {len(top_sellers)} net sellers among institutional holders. See tables below for details.</p>
    </div>

    <div class="wb-section">
      <div class="wb-section-title"><span class="wb-badge blind">BLIND SPOTS</span> What This App Cannot See</div>
      <ul class="blind-spots-list">
        <li><span class="bs-icon">X</span><span><strong>Institutions under $100M AUM</strong> -- no 13F filing required. Completely invisible to public data.<br><span class="bs-fix">Potential fix: Bloomberg DL intraday trade-size clustering could distinguish institutional block trades from retail flow.</span></span></li>
        <li><span class="bs-icon">X</span><span><strong>Family offices</strong> -- exempt from 13F since Dodd-Frank (2011). SEB is exactly the type of illiquid, high-value stock family offices favor.<br><span class="bs-fix">Potential fix: No public data solution. Relationship-based intelligence only (IR outreach, prime broker channels).</span></span></li>
        <li><span class="bs-icon">X</span><span><strong>Foreign institutions</strong> -- no US filing obligation unless crossing 5% ownership.<br><span class="bs-fix">Potential fix: Monitor international regulatory filings; Bloomberg DL global ownership data may surface non-US holders.</span></span></li>
        <li><span class="bs-icon">X</span><span><strong>Sub-5% accumulators</strong> -- no 13D/13G triggered until the 5% threshold (~47,900 shares of float). Someone could accumulate ~$200M+ worth without any disclosure.<br><span class="bs-fix">Potential fix: None from public data. Watch for 13D/13G filings; this app monitors EDGAR in real-time.</span></span></li>
        <li><span class="bs-icon">X</span><span><strong>Multiple-account strategies</strong> -- a single entity using multiple brokers/accounts to stay below reporting thresholds.<br><span class="bs-fix">Potential fix: Trade pattern analysis from intraday data (Bloomberg DL) can sometimes identify correlated execution patterns.</span></span></li>
        <li><span class="bs-icon">X</span><span><strong>Retail investors</strong> -- never required to file. However, SEB's $5,000 price point and low liquidity make significant retail accumulation unlikely.<br><span class="bs-fix">No fix needed -- retail is unlikely to drive sustained 8.9x volume increase at this price level.</span></span></li>
      </ul>
    </div>
  </div>

  <!-- ROW 4: OBV + A/D Charts -->
  <div class="row-2">
    <div class="card">
      <div class="card-title">On-Balance Volume (OBV)</div>
      <div class="chart-container"><canvas id="obvChart"></canvas></div>
      <p style="font-size:11px;color:var(--text-muted);margin-top:8px;">OBV adds volume on up-days, subtracts on down-days. Rising OBV + rising price = accumulation confirmation.</p>
    </div>
    <div class="card">
      <div class="card-title">Accumulation / Distribution Line</div>
      <div class="chart-container"><canvas id="adChart"></canvas></div>
      <p style="font-size:11px;color:var(--text-muted);margin-top:8px;">Weights volume by where price closes within the day's range. Closing near highs on heavy volume = buying pressure.</p>
    </div>
  </div>

  <!-- ROW 5: Price + Accum Score -->
  <div class="card">
    <div class="card-title">Accumulation Score &amp; Price History</div>
    <div class="chart-container tall"><canvas id="priceAccumChart"></canvas></div>
  </div>

  <!-- ROW 6: Institutional Holders -->
  <div class="row-2">
    <div class="card">
      <div class="card-title">Top Buyers (Q4 2025 13F)</div>
      <div class="table-scroll">
        <table><thead><tr><th>Holder</th><th>Position</th><th>Change</th><th>% Out</th><th>Type</th></tr></thead>
        <tbody>{buyers_html or '<tr><td colspan="5" style="color:var(--text-muted)">No HDS data</td></tr>'}</tbody></table>
      </div>
    </div>
    <div class="card">
      <div class="card-title">Top Sellers (Q4 2025 13F)</div>
      <div class="table-scroll">
        <table><thead><tr><th>Holder</th><th>Position</th><th>Change</th><th>% Out</th><th>Type</th></tr></thead>
        <tbody>{sellers_html or '<tr><td colspan="5" style="color:var(--text-muted)">No HDS data</td></tr>'}</tbody></table>
      </div>
    </div>
  </div>

  <!-- ROW 7: SEC Filings -->
  <div class="card">
    <div class="card-title">SEC EDGAR Filings (13D / 13G / Form 4)</div>
    <div class="table-scroll">
      <table><thead><tr><th>Date</th><th>Form</th><th>Description</th></tr></thead>
      <tbody>{filings_html or '<tr><td colspan="3" style="color:var(--text-muted)">No recent ownership filings</td></tr>'}</tbody></table>
    </div>
  </div>

  <!-- ROW 8: Cause Breakdown (Market vs SEB-specific) -->
  <div class="row-2">
    <div class="card">
      <div class="card-title">Today's Cause Breakdown</div>
      <div class="bar-row"><span class="bar-label">Market</span><div class="bar-track"><div class="bar-fill market" style="width:{min(abs(latest['market_pct'])/max(abs(latest['ret']),0.01)*100,100):.0f}%">{'+'if latest['market_pct']>0 else''}{latest['market_pct']:.2f}%</div></div></div>
      <div class="bar-row"><span class="bar-label">SEB-Specific</span><div class="bar-track"><div class="bar-fill company" style="width:{min(abs(latest['seb_specific_pct'])/max(abs(latest['ret']),0.01)*100,100):.0f}%">{'+'if latest['seb_specific_pct']>0 else''}{latest['seb_specific_pct']:.2f}%</div></div></div>
      <p style="margin-top:12px;font-size:11px;color:var(--text-muted);">Market = S&amp;P 500 return. SEB-Specific = everything beyond the market move.</p>
      <div class="note-box"><strong>Note:</strong> Seaboard is a diversified conglomerate (pork, commodity trading, milling, marine transportation). No single sector ETF is an appropriate peer. The decomposition chart below uses Consumer Staples (IYK) as a <em>rough reference only</em> -- it does not represent Seaboard's true sector exposure and should not be cited in board communications.</div>
    </div>
    <div class="card">
      <div class="card-title">Market vs SEB-Specific (60d)</div>
      <div class="chart-container"><canvas id="decompChart"></canvas></div>
    </div>
  </div>

  <!-- ROW 9: Stats -->
  <div class="card">
    <div class="card-title">Summary Statistics</div>
    <div class="stats-bar">
      <div class="stat-box"><div class="metric-label">Avg Return</div><div class="metric-value {'up'if stats['avg_ret']>0 else'down'}">{'+'if stats['avg_ret']>0 else''}{stats['avg_ret']}%</div></div>
      <div class="stat-box"><div class="metric-label">Volatility</div><div class="metric-value">{stats['vol']}%</div></div>
      <div class="stat-box"><div class="metric-label">Up Days</div><div class="metric-value up">{stats['up']}</div></div>
      <div class="stat-box"><div class="metric-label">Down Days</div><div class="metric-value down">{stats['down']}</div></div>
      <div class="stat-box"><div class="metric-label">Avg Volume</div><div class="metric-value">{stats['avg_volume']:,}</div></div>
      <div class="stat-box"><div class="metric-label">Vol Regime</div><div class="metric-value {regime_cls}">{stats['regime_shift']}x</div></div>
      <div class="stat-box"><div class="metric-label">Accum Score</div><div class="metric-value {score_cls}">{score}/100</div></div>
      <div class="stat-box"><div class="metric-label">Anomalies (90d)</div><div class="metric-value" style="color:var(--amber)">{stats['anomaly_count']}</div></div>
    </div>
  </div>

  <!-- ROW 10: Anomalies + Verdict History -->
  <div class="row-2">
    <div class="card">
      <div class="card-title">Anomaly Alerts (Last 90 Days)</div>
      <div class="anomaly-list">{anomaly_html or '<div style="color:var(--text-muted);font-size:13px;">No anomalies detected.</div>'}</div>
    </div>
    <div class="card">
      <div class="card-title">Verdict History (Last 30 Days)</div>
      <div class="verdict-history">{verdict_hist_html}</div>
    </div>
  </div>

</div>

<script>
const D = {json.dumps(records)};
const cc = {{bg:'#1a2332',border:'#1e2d3d',text:'#8896a8',green:'#22c55e',red:'#ef4444',blue:'#3b82f6',amber:'#f59e0b'}};
const tt = {{backgroundColor:cc.bg,borderColor:cc.border,borderWidth:1,titleColor:'#e2e8f0',bodyColor:'#e2e8f0'}};
const gs = {{color:'rgba(30,45,61,0.5)'}};
const ts = {{color:cc.text,font:{{size:10}}}};

// Volume Regime
new Chart(document.getElementById('volumeRegimeChart'),{{
  type:'bar',data:{{labels:D.map(d=>d.date),datasets:[
    {{label:'Volume',data:D.map(d=>d.volume),backgroundColor:D.map(d=>d.volume>(d.vol_avg_20||0)*2?'rgba(239,68,68,0.6)':d.volume>(d.vol_avg_20||0)*1.5?'rgba(245,158,11,0.5)':'rgba(59,130,246,0.3)'),borderWidth:0}},
    {{label:'20d Avg',data:D.map(d=>d.vol_avg_20||0),type:'line',borderColor:cc.amber,borderWidth:2,pointRadius:0,tension:0.3}}
  ]}},options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{labels:{{color:cc.text,font:{{size:10}}}}}},tooltip:tt}},scales:{{x:{{type:'time',time:{{unit:'month'}},ticks:ts,grid:gs}},y:{{ticks:{{...ts,callback:v=>(v/1000).toFixed(0)+'K'}},grid:gs}}}}}}
}});

// OBV
new Chart(document.getElementById('obvChart'),{{
  type:'line',data:{{labels:D.map(d=>d.date),datasets:[{{label:'OBV',data:D.map(d=>d.obv),borderColor:cc.green,backgroundColor:'rgba(34,197,94,0.08)',fill:true,tension:0.3,pointRadius:0,borderWidth:2}}]}},
  options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{labels:{{color:cc.text,font:{{size:10}}}}}},tooltip:tt}},scales:{{x:{{type:'time',time:{{unit:'month'}},ticks:ts,grid:gs}},y:{{ticks:{{...ts,callback:v=>(v/1000000).toFixed(1)+'M'}},grid:gs}}}}}}
}});

// A/D Line
new Chart(document.getElementById('adChart'),{{
  type:'line',data:{{labels:D.map(d=>d.date),datasets:[{{label:'Cumulative A/D',data:D.map(d=>d.cum_ad),borderColor:'#8b5cf6',backgroundColor:'rgba(139,92,246,0.08)',fill:true,tension:0.3,pointRadius:0,borderWidth:2}}]}},
  options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{labels:{{color:cc.text,font:{{size:10}}}}}},tooltip:tt}},scales:{{x:{{type:'time',time:{{unit:'month'}},ticks:ts,grid:gs}},y:{{ticks:{{...ts,callback:v=>(v/1000000).toFixed(1)+'M'}},grid:gs}}}}}}
}});

// Price + Accum Score
new Chart(document.getElementById('priceAccumChart'),{{
  type:'line',data:{{labels:D.map(d=>d.date),datasets:[
    {{label:'SEB Close',data:D.map(d=>d.close),borderColor:cc.blue,backgroundColor:'rgba(59,130,246,0.08)',fill:true,tension:0.3,pointRadius:0,borderWidth:2,yAxisID:'y'}},
    {{label:'Volume',data:D.map(d=>d.volume),type:'bar',backgroundColor:D.map(d=>d.ret>=0?'rgba(34,197,94,0.25)':'rgba(239,68,68,0.25)'),borderWidth:0,yAxisID:'y2',barPercentage:0.8}},
    {{label:'Accum Score',data:D.map(d=>d.accum_score),borderColor:cc.amber,borderWidth:2,borderDash:[4,4],pointRadius:0,tension:0.3,yAxisID:'y1'}}
  ]}},options:{{responsive:true,maintainAspectRatio:false,interaction:{{mode:'index',intersect:false}},plugins:{{legend:{{labels:{{color:cc.text,font:{{size:11}}}}}},tooltip:tt}},scales:{{x:{{type:'time',time:{{unit:'month'}},ticks:ts,grid:gs}},y:{{position:'left',ticks:{{...ts,callback:v=>'$'+v.toLocaleString()}},grid:gs}},y1:{{position:'right',min:0,max:100,ticks:{{...ts,callback:v=>v+'/100'}},grid:{{display:false}}}},y2:{{display:false}}}}}}
}});

// PDF Download: convert canvases to images, then print
function downloadPDF() {{
  // Convert all Chart.js canvases to static images for print
  const canvases = document.querySelectorAll('canvas');
  const images = [];
  canvases.forEach(canvas => {{
    const img = document.createElement('img');
    img.src = canvas.toDataURL('image/png', 1.0);
    img.className = 'chart-img';
    img.style.display = 'none';
    canvas.parentNode.insertBefore(img, canvas.nextSibling);
    images.push(img);
  }});
  // Brief delay to ensure images render, then print
  setTimeout(() => {{
    window.print();
    // Clean up images after print dialog closes
    setTimeout(() => {{
      images.forEach(img => img.remove());
    }}, 1000);
  }}, 300);
}}

// Decomposition (Market vs SEB-specific)
const last60 = D.slice(-60);
new Chart(document.getElementById('decompChart'),{{
  type:'bar',data:{{labels:last60.map(d=>d.date),datasets:[
    {{label:'Market (S&P 500)',data:last60.map(d=>d.market_pct),backgroundColor:'#6366f1',borderWidth:0}},
    {{label:'SEB-Specific',data:last60.map(d=>d.seb_specific_pct),backgroundColor:'#3b82f6',borderWidth:0}}
  ]}},options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{labels:{{color:cc.text,font:{{size:10}}}}}},tooltip:tt}},scales:{{x:{{type:'time',time:{{unit:'week'}},stacked:true,ticks:ts,grid:gs}},y:{{stacked:true,ticks:{{...ts,callback:v=>v.toFixed(1)+'%'}},grid:gs}}}}}}
}});
</script>
</body>
</html>"""
    return html


# =============================================================================
# MAIN
# =============================================================================

def main():
    print("=" * 60)
    print("SEB Stock Intelligence Dashboard v3")
    print("=" * 60)

    tickers = fetch_price_data()
    records = build_daily_records(tickers)
    print(f"Built {len(records)} daily records")

    vol_shift = compute_accumulation(records)
    print(f"Volume regime shift: {vol_shift:.1f}x (first 60d -> last 60d)")
    print(f"Latest accumulation score: {records[-1].get('accum_score', 'N/A')}/100")

    bp = compute_buying_profile(records)
    print(f"Buyer profile: {bp.get('buyer_type', 'N/A')}")
    print(f"  Regime started: {bp.get('regime_start')}")
    print(f"  Up-volume ratio: {bp.get('up_vol_pct')}%")
    print(f"  Volume CV: {bp.get('vol_cv')}%")
    print(f"  Est net shares: {bp.get('est_net_shares', 0):,}")

    anomalies = compute_anomalies(records)
    print(f"Detected {sum(len(a['flags']) for a in anomalies)} anomaly flags")

    holders = load_hds_data()
    holder_changes = build_holder_changes(holders) if holders else []
    print(f"Holder changes: {len([h for h in holder_changes if h['change'] > 0])} buyers, {len([h for h in holder_changes if h['change'] < 0])} sellers")

    edgar = fetch_edgar_filings()

    html = generate_html(records, anomalies, vol_shift, holder_changes, edgar, bp)
    OUTPUT_FILE.write_text(html, encoding="utf-8")
    print(f"\nDashboard written to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
