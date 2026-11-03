#!/usr/bin/env python3
"""Performance budget for replica-seo. Standard library only, never touches the network.

Reads Lighthouse JSON reports (from `npx lighthouse <url> --output=json`, from
Chrome DevTools > Lighthouse > Save as JSON, or a PageSpeed Insights API
response) and checks them against Core Web Vitals and a page budget. With
--baseline it compares your clone with the original, page by page.

    python3 perf.py replica/seo/lh/clone-home-mobile.json
    python3 perf.py replica/seo/lh/clone-*.json --baseline replica/seo/lh/original-*.json
    python3 perf.py report.json --budget-kb 1200 --json
    python3 perf.py replica/seo/lh/clone-*.json --gate     # exit 1 if any metric is poor

Thresholds are Google's (good / poor):

    LCP  Largest Contentful Paint   2.5 s  / 4.0 s
    CLS  Cumulative Layout Shift    0.10   / 0.25
    INP  Interaction to Next Paint  200 ms / 500 ms   (field data only)
    TBT  Total Blocking Time        200 ms / 600 ms   (lab proxy for INP)
    FCP  First Contentful Paint     1.8 s  / 3.0 s
    SI   Speed Index                3.4 s  / 5.8 s

Lab numbers (Lighthouse) are one run on one machine; run 3 times and keep the
median report. Field numbers (PageSpeed Insights "loadingExperience") are real
Chrome users over 28 days and win when both exist.
"""

import argparse
import glob
import json
import os
import sys

# key: (label, lighthouse audit id, unit, good, poor)
METRICS = [
    ("LCP", "largest-contentful-paint", "ms", 2500, 4000),
    ("CLS", "cumulative-layout-shift", "", 0.10, 0.25),
    ("TBT", "total-blocking-time", "ms", 200, 600),
    ("FCP", "first-contentful-paint", "ms", 1800, 3000),
    ("SI", "speed-index", "ms", 3400, 5800),
]
FIELD = {  # PSI loadingExperience keys -> (label, divisor, good, poor)
    "LARGEST_CONTENTFUL_PAINT_MS": ("LCP", 1, 2500, 4000),
    "CUMULATIVE_LAYOUT_SHIFT_SCORE": ("CLS", 100, 0.10, 0.25),
    "INTERACTION_TO_NEXT_PAINT": ("INP", 1, 200, 500),
}


class ReportError(Exception):
    pass


def rate(value, good, poor):
    if value is None:
        return "n/a"
    if value <= good:
        return "good"
    if value <= poor:
        return "needs work"
    return "poor"


def load(path):
    with open(path, encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except ValueError:
            raise ReportError("%s is not JSON" % path)
    lhr = data.get("lighthouseResult", data)
    if "audits" not in lhr:
        raise ReportError("%s is not a Lighthouse report (no 'audits')" % path)
    return data, lhr


def read(path, budget_kb=1600):
    data, lhr = load(path)
    audits = lhr["audits"]
    cat = (lhr.get("categories") or {}).get("performance") or {}
    score = cat.get("score")
    out = {"report": os.path.basename(path),
           "url": lhr.get("finalDisplayedUrl") or lhr.get("finalUrl") or lhr.get("requestedUrl"),
           "device": ((lhr.get("configSettings") or {}).get("formFactor")
                      or (lhr.get("configSettings") or {}).get("emulatedFormFactor") or "?"),
           "score": round(score * 100) if isinstance(score, (int, float)) else None,
           "lab": {}, "field": {}, "weight_kb": None, "requests": None,
           "opportunities": [], "fails": []}
    for label, aid, unit, good, poor in METRICS:
        v = (audits.get(aid) or {}).get("numericValue")
        out["lab"][label] = {"value": v, "unit": unit, "rating": rate(v, good, poor)}
    w = (audits.get("total-byte-weight") or {}).get("numericValue")
    if w is not None:
        out["weight_kb"] = round(w / 1024)
        if out["weight_kb"] > budget_kb:
            out["fails"].append("page weight %d KB over budget %d KB" % (out["weight_kb"], budget_kb))
    items = ((audits.get("network-requests") or {}).get("details") or {}).get("items")
    if isinstance(items, list):
        out["requests"] = len(items)
    for aid, a in audits.items():
        det = a.get("details") or {}
        saving = det.get("overallSavingsMs")
        kb = det.get("overallSavingsBytes")
        if det.get("type") == "opportunity" and ((saving or 0) >= 100 or (kb or 0) >= 20 * 1024):
            out["opportunities"].append({"id": aid, "title": a.get("title", aid),
                                         "saves_ms": round(saving or 0),
                                         "saves_kb": round((kb or 0) / 1024)})
    out["opportunities"].sort(key=lambda o: (-o["saves_ms"], -o["saves_kb"]))
    metrics = (data.get("loadingExperience") or {}).get("metrics") or {}
    for key, (label, div, good, poor) in FIELD.items():
        m = metrics.get(key)
        if m and "percentile" in m:
            v = m["percentile"] / div
            out["field"][label] = {"value": v, "rating": rate(v, good, poor)}
    # what counts as poor: field wins over lab for LCP/CLS; INP only from field
    for label in ("LCP", "CLS", "INP", "TBT"):
        r = (out["field"].get(label) or out["lab"].get(label) or {}).get("rating")
        if r == "poor":
            out["fails"].append("%s is poor" % label)
    return out


def fmt(label, v):
    if v is None:
        return "-"
    if label == "CLS":
        return "%.3f" % v
    if label in ("INP", "TBT"):
        return "%d ms" % v
    return "%.1f s" % (v / 1000.0)


def pair(reports, baselines):
    """Match clone and original reports by device, then by file order."""
    pairs = []
    pool = list(baselines)
    for r in reports:
        match = next((b for b in pool if b["device"] == r["device"]), None) or (pool[0] if pool else None)
        if match:
            pool.remove(match)
        pairs.append((r, match))
    return pairs


def render(pairs, markdown=False):
    lines = []
    for r, b in pairs:
        head = "%s  [%s]  performance %s/100" % (r["url"] or r["report"], r["device"],
                                                 r["score"] if r["score"] is not None else "?")
        if b and b["score"] is not None and r["score"] is not None:
            head += "  (original %d, %+d)" % (b["score"], r["score"] - b["score"])
        lines.append(("### " if markdown else "") + head)
        lines.append("")
        lines.append("| metric | clone | rating | original |" if b else "| metric | value | rating |")
        lines.append("| --- | --- | --- | --- |" if b else "| --- | --- | --- |")
        for label, *_ in METRICS:
            m = r["lab"][label]
            row = "| %s (lab) | %s | %s |" % (label, fmt(label, m["value"]), m["rating"])
            if b:
                row += " %s |" % fmt(label, b["lab"][label]["value"])
            lines.append(row)
        for label, m in r["field"].items():
            row = "| %s (field) | %s | %s |" % (label, fmt(label, m["value"]), m["rating"])
            if b:
                bm = b["field"].get(label)
                row += " %s |" % (fmt(label, bm["value"]) if bm else "-")
            lines.append(row)
        if r["weight_kb"] is not None:
            row = "| weight | %d KB | |" % r["weight_kb"]
            if b:
                row += " %s |" % ("%d KB" % b["weight_kb"] if b["weight_kb"] is not None else "-")
            lines.append(row)
        if r["requests"] is not None:
            row = "| requests | %d | |" % r["requests"]
            if b:
                row += " %s |" % (b["requests"] if b["requests"] is not None else "-")
            lines.append(row)
        if r["opportunities"]:
            lines.append("")
            lines.append("Top fixes:")
            for o in r["opportunities"][:5]:
                lines.append("- %s (saves ~%d ms, %d KB)" % (o["title"], o["saves_ms"], o["saves_kb"]))
        if r["fails"]:
            lines.append("")
            lines.append("FAILS: " + "; ".join(r["fails"]))
        lines.append("")
    return "\n".join(lines).rstrip()


def expand(patterns):
    files = []
    for p in patterns:
        hits = glob.glob(p)
        files.extend(sorted(hits) if hits else [p])
    return files


def main(argv=None):
    ap = argparse.ArgumentParser(description="Core Web Vitals budget from Lighthouse JSON.")
    ap.add_argument("reports", nargs="+")
    ap.add_argument("--baseline", nargs="+", help="the original's Lighthouse reports")
    ap.add_argument("--budget-kb", type=int, default=1600)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--markdown", action="store_true")
    ap.add_argument("--gate", action="store_true", help="exit 1 if any page fails")
    args = ap.parse_args(argv)
    try:
        reports = [read(p, args.budget_kb) for p in expand(args.reports)]
        bases = [read(p, args.budget_kb) for p in expand(args.baseline)] if args.baseline else []
    except (ReportError, OSError) as exc:
        print("perf: %s" % exc, file=sys.stderr)
        return 2
    pairs = pair(reports, bases)
    if args.json:
        print(json.dumps([{"clone": r, "original": b} for r, b in pairs], indent=2, ensure_ascii=False))
    else:
        print(render(pairs, args.markdown))
    if args.gate and any(r["fails"] for r in reports):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
