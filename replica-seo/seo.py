#!/usr/bin/env python3
"""SEO audit for replica-seo. Standard library only, never touches the network.

Reads HTML files you saved (from the original's public pages, or your clone's
build output) and reports on-page, technical, social, structured data and
local SEO problems, ranked by severity, with a 0-100 score.

    python3 seo.py replica/seo/original/            # a folder of saved pages
    python3 seo.py out/index.html out/about.html    # or single files
    python3 seo.py out/ --site out/                 # also check robots.txt, sitemap.xml
    python3 seo.py out/ --baseline replica/seo/original/   # clone vs original
    python3 seo.py out/ --local                     # local business checks (NAP, LocalBusiness)
    python3 seo.py out/ --json > replica/seo/clone.json
    python3 seo.py out/ --fail-under 85             # exit 1 below the score (deploy gate)

Severity weights: S1 (blocks indexing or ranking) 10, S2 (costs rankings or
clicks) 4, S3 (polish) 1. The score starts at 100 per page and loses the
weights of its issues, floored at 0; the site score is the page average minus
site-wide issues (duplicate titles, missing robots.txt or sitemap).

It checks what static HTML can prove. It cannot see what JavaScript renders
after load, real-world speed, backlinks or rankings. Use perf.py with a
Lighthouse report for speed, and Search Console for the rest.
"""

import argparse
import json
import os
import re
import sys
from html.parser import HTMLParser

WEIGHT = {"S1": 10, "S2": 4, "S3": 1}
TITLE_MIN, TITLE_MAX = 30, 60
DESC_MIN, DESC_MAX = 70, 160
THIN_WORDS = 250
HEAVY_HTML = 150 * 1024
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr"}
SKIP_TEXT = {"script", "style", "noscript", "template", "svg"}
LOCAL_TYPES = {"localbusiness", "restaurant", "store", "medicalbusiness",
               "healthandbeautybusiness", "foodestablishment", "professionalservice",
               "lodgingbusiness", "hotel", "physician", "dentist", "dietnutrition",
               "sportsactivitylocation", "homeandconstructionbusiness", "organization"}


class Page(HTMLParser):
    """Collects everything the checks need in one pass."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lang = None
        self.title = None
        self._in_title = False
        self._title_buf = []
        self.metas = []          # dicts of attrs
        self.links = []          # <link> attrs
        self.headings = []       # (level, text)
        self._heading = None
        self._heading_buf = []
        self.images = []         # attrs
        self.anchors = []        # attrs
        self.head_scripts = []   # attrs of <script src> inside <head>
        self.jsonld = []         # raw strings
        self._in_jsonld = False
        self._jsonld_buf = []
        self.iframes = 0
        self._stack = []
        self._skip = 0
        self.words = 0
        self.text_sample = []
        self.in_head = False
        self.has_tel = False

    # --- parsing -------------------------------------------------------
    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v if v is not None else "") for k, v in attrs}
        if tag == "html":
            self.lang = a.get("lang")
        elif tag == "head":
            self.in_head = True
        elif tag == "body":
            self.in_head = False
        elif tag == "title" and self.title is None:
            self._in_title = True
        elif tag == "meta":
            self.metas.append(a)
        elif tag == "link":
            self.links.append(a)
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._heading = int(tag[1])
            self._heading_buf = []
        elif tag == "img":
            self.images.append(a)
        elif tag == "a":
            self.anchors.append(a)
            if a.get("href", "").lower().startswith("tel:"):
                self.has_tel = True
        elif tag == "iframe":
            self.iframes += 1
        elif tag == "script":
            if a.get("type", "").lower() == "application/ld+json":
                self._in_jsonld = True
                self._jsonld_buf = []
            elif self.in_head and a.get("src"):
                self.head_scripts.append(a)
        if tag in SKIP_TEXT:
            self._skip += 1
        if tag not in VOID:
            self._stack.append(tag)

    def handle_endtag(self, tag):
        if tag == "title" and self._in_title:
            self._in_title = False
            self.title = " ".join("".join(self._title_buf).split())
        elif tag == "head":
            self.in_head = False
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6") and self._heading:
            self.headings.append((self._heading, " ".join("".join(self._heading_buf).split())))
            self._heading = None
        elif tag == "script" and self._in_jsonld:
            self._in_jsonld = False
            self.jsonld.append("".join(self._jsonld_buf))
        if tag in SKIP_TEXT and self._skip:
            self._skip -= 1
        if tag in self._stack:
            while self._stack and self._stack.pop() != tag:
                pass

    def handle_data(self, data):
        if self._in_title:
            self._title_buf.append(data)
        if self._in_jsonld:
            self._jsonld_buf.append(data)
            return
        if self._heading:
            self._heading_buf.append(data)
        if self._skip or self.in_head:
            return
        words = re.findall(r"\w+", data, re.U)
        self.words += len(words)
        if len(self.text_sample) < 400:
            self.text_sample.extend(words[:50])

    # --- helpers -------------------------------------------------------
    def meta(self, key, attr="name"):
        for m in self.metas:
            if m.get(attr, "").lower() == key:
                return m.get("content", "")
        return None

    def link(self, rel):
        out = []
        for l in self.links:
            if rel in l.get("rel", "").lower().split():
                out.append(l)
        return out


def jsonld_types(raw_blocks):
    """Return (types, errors) from JSON-LD blocks. Types are lowercased."""
    types, errors, nodes = [], [], []
    for raw in raw_blocks:
        try:
            data = json.loads(raw)
        except ValueError as exc:
            errors.append(str(exc).split(":")[0])
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                nodes.append(node)
                t = node.get("@type")
                for one in (t if isinstance(t, list) else [t]):
                    if isinstance(one, str):
                        types.append(one.lower())
                if "@graph" in node:
                    stack.append(node["@graph"])
    return types, errors, nodes


def issue(sev, code, msg, fix):
    return {"severity": sev, "code": code, "message": msg, "fix": fix}


def audit_page(html, name="page", local=False):
    p = Page()
    try:
        p.feed(html)
        p.close()
    except Exception as exc:  # malformed HTML should not crash the audit
        return {"page": name, "score": 0, "issues": [
            issue("S1", "parse", "HTML could not be parsed: %s" % exc, "Fix the markup.")],
            "facts": {}}
    out = []

    # indexing ----------------------------------------------------------
    robots = (p.meta("robots") or "").lower()
    if "noindex" in robots:
        out.append(issue("S1", "noindex", "meta robots says noindex: this page will not be indexed.",
                         "Remove noindex on pages that should rank."))
    canon = p.link("canonical")
    if not canon:
        out.append(issue("S2", "canonical-missing", "No rel=canonical.",
                         "Add <link rel=\"canonical\" href=\"https://yourdomain/path\">."))
    elif len(canon) > 1:
        out.append(issue("S1", "canonical-multiple", "%d canonical tags; Google may ignore all." % len(canon),
                         "Keep exactly one canonical."))
    elif not re.match(r"^https?://", canon[0].get("href", "")):
        out.append(issue("S2", "canonical-relative", "Canonical is not an absolute URL.",
                         "Use the full https:// URL."))

    # title & description ----------------------------------------------
    t = p.title or ""
    if not t:
        out.append(issue("S1", "title-missing", "No <title>.", "Write a unique 30-60 character title."))
    elif len(t) < TITLE_MIN:
        out.append(issue("S2", "title-short", "Title is %d characters: '%s'." % (len(t), t),
                         "Aim for 30-60 with the main keyword and the brand."))
    elif len(t) > TITLE_MAX:
        out.append(issue("S3", "title-long", "Title is %d characters and will be cut in results." % len(t),
                         "Keep it under 60, keyword first."))
    d = p.meta("description")
    if d is None or not d.strip():
        out.append(issue("S2", "description-missing", "No meta description.",
                         "Write 70-160 characters that sell the click."))
    elif len(d) < DESC_MIN:
        out.append(issue("S3", "description-short", "Meta description is %d characters." % len(d),
                         "Aim for 70-160."))
    elif len(d) > DESC_MAX:
        out.append(issue("S3", "description-long", "Meta description is %d characters." % len(d),
                         "Keep it under 160."))

    # document basics ---------------------------------------------------
    if not p.lang:
        out.append(issue("S2", "lang-missing", "<html> has no lang attribute.",
                         "Add lang=\"es-MX\" (or the page's language)."))
    vp = p.meta("viewport")
    if not vp:
        out.append(issue("S1", "viewport-missing", "No meta viewport: fails mobile-first indexing.",
                         "Add <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">."))
    elif "user-scalable=no" in vp.replace(" ", "").lower() or "maximum-scale=1" in vp.replace(" ", "").lower():
        out.append(issue("S3", "viewport-zoom", "Viewport blocks zoom (accessibility).",
                         "Remove user-scalable=no and maximum-scale=1."))
    if not any(m.get("charset") for m in p.metas) and \
            not any(m.get("http-equiv", "").lower() == "content-type" for m in p.metas):
        out.append(issue("S3", "charset-missing", "No charset declared.", "Add <meta charset=\"utf-8\"> first in <head>."))

    # headings ----------------------------------------------------------
    h1 = [h for h in p.headings if h[0] == 1]
    if not h1:
        out.append(issue("S2", "h1-missing", "No <h1>.", "One <h1> stating what the page is about."))
    elif len(h1) > 1:
        out.append(issue("S3", "h1-multiple", "%d <h1> tags." % len(h1), "Keep one; demote the rest to <h2>."))
    if any(not h[1] for h in p.headings):
        out.append(issue("S3", "heading-empty", "Empty heading tag(s).", "Remove or fill them."))
    prev = 0
    for level, _ in p.headings:
        if prev and level > prev + 1:
            out.append(issue("S3", "heading-skip", "Heading jumps from h%d to h%d." % (prev, level),
                             "Do not skip levels; it breaks the outline."))
            break
        prev = level

    # content -----------------------------------------------------------
    if p.words < THIN_WORDS:
        out.append(issue("S2", "thin-content", "Only %d words of visible text." % p.words,
                         "Pages meant to rank need substance (250+ words), or the content "
                         "is rendered by JavaScript and crawlers may miss it."))

    # images ------------------------------------------------------------
    no_alt = [i for i in p.images if "alt" not in i]
    if no_alt:
        out.append(issue("S2", "img-alt", "%d of %d images have no alt attribute." % (len(no_alt), len(p.images)),
                         "Describe each image; use alt=\"\" only for decoration."))
    no_dim = [i for i in p.images if not (i.get("width") and i.get("height"))]
    if no_dim:
        out.append(issue("S2", "img-dimensions", "%d images without width and height (layout shift, CLS)." % len(no_dim),
                         "Set width and height (or aspect-ratio in CSS)."))
    if len(p.images) > 3:
        lazy = [i for i in p.images if i.get("loading", "").lower() == "lazy"]
        if not lazy:
            out.append(issue("S3", "img-lazy", "No image uses loading=\"lazy\".",
                             "Lazy-load images below the fold (never the hero/LCP image)."))
    legacy = [i for i in p.images if re.search(r"\.(png|jpe?g|gif|bmp)(\?|$)", i.get("src", ""), re.I)]
    if len(legacy) > 2 and not any(re.search(r"\.(webp|avif)", i.get("src", "") + i.get("srcset", ""), re.I)
                                   for i in p.images):
        out.append(issue("S3", "img-format", "%d images in PNG/JPG/GIF and none in WebP/AVIF." % len(legacy),
                         "Serve WebP or AVIF; usually 25-50%% lighter."))

    # performance hints in markup --------------------------------------
    blocking = [s for s in p.head_scripts if "async" not in s and "defer" not in s
                and s.get("type", "").lower() != "module"]
    if blocking:
        out.append(issue("S2", "render-blocking-js", "%d render-blocking scripts in <head>." % len(blocking),
                         "Add defer (or async), or move them to the end of <body>."))
    css = [l for l in p.link("stylesheet")]
    if len(css) > 4:
        out.append(issue("S3", "many-stylesheets", "%d stylesheets in <head>." % len(css),
                         "Bundle CSS and inline the critical part."))
    if len(html.encode("utf-8")) > HEAVY_HTML:
        out.append(issue("S3", "heavy-html", "HTML is %d KB." % (len(html.encode("utf-8")) // 1024),
                         "Trim inline data, SVG and builder markup."))

    # links -------------------------------------------------------------
    blank_unsafe = [a for a in p.anchors if a.get("target") == "_blank"
                    and "noopener" not in a.get("rel", "") and "noreferrer" not in a.get("rel", "")]
    if blank_unsafe:
        out.append(issue("S3", "blank-noopener", "%d links open in a new tab without rel=noopener." % len(blank_unsafe),
                         "Add rel=\"noopener\"."))
    if not p.anchors:
        out.append(issue("S2", "no-links", "No links on the page.", "Link to related pages; orphan pages rank poorly."))

    # social ------------------------------------------------------------
    og_missing = [k for k in ("og:title", "og:description", "og:image")
                  if not p.meta(k, "property")]
    if og_missing:
        out.append(issue("S3", "open-graph", "Missing %s." % ", ".join(og_missing),
                         "Add Open Graph tags so shares on WhatsApp/Facebook look right."))
    if not p.link("icon") and not p.link("shortcut"):
        out.append(issue("S3", "favicon", "No favicon link.", "Add <link rel=\"icon\" href=\"/favicon.ico\">."))

    # structured data ---------------------------------------------------
    types, errors, nodes = jsonld_types(p.jsonld)
    for e in errors:
        out.append(issue("S2", "jsonld-invalid", "A JSON-LD block is invalid JSON (%s)." % e,
                         "Fix it; invalid blocks are ignored entirely."))
    if not p.jsonld:
        out.append(issue("S3", "jsonld-missing", "No structured data (JSON-LD).",
                         "Add schema.org markup (Organization, LocalBusiness, Product, Article...)."))

    # local SEO ---------------------------------------------------------
    if local:
        local_nodes = [n for n in nodes if any(
            isinstance(t, str) and t.lower() in LOCAL_TYPES
            for t in (n.get("@type") if isinstance(n.get("@type"), list) else [n.get("@type")]))]
        if not local_nodes:
            out.append(issue("S2", "local-schema", "No LocalBusiness (or subtype) structured data.",
                             "Add LocalBusiness JSON-LD with name, address, telephone, geo, openingHours."))
        else:
            n = local_nodes[0]
            missing = [k for k in ("name", "address", "telephone") if not n.get(k)]
            if missing:
                out.append(issue("S2", "local-nap", "LocalBusiness is missing %s." % ", ".join(missing),
                                 "Name, address and phone must match Google Business Profile exactly."))
            extra = [k for k in ("geo", "openingHours", "openingHoursSpecification", "url") if n.get(k)]
            if not any(k in extra for k in ("openingHours", "openingHoursSpecification")):
                out.append(issue("S3", "local-hours", "No opening hours in LocalBusiness.",
                                 "Add openingHoursSpecification."))
        if not p.has_tel:
            out.append(issue("S3", "local-tel", "No tap-to-call (tel:) link.",
                             "Link the phone number with href=\"tel:+52...\"."))

    score = max(0, 100 - sum(WEIGHT[i["severity"]] for i in out))
    facts = {"title": t, "description": d, "lang": p.lang, "words": p.words,
             "h1": [h[1] for h in h1], "images": len(p.images), "jsonld_types": sorted(set(types)),
             "canonical": canon[0].get("href") if canon else None,
             "hreflang": sorted({l.get("hreflang") for l in p.link("alternate") if l.get("hreflang")})}
    return {"page": name, "score": score, "issues": out, "facts": facts}


def collect(paths):
    files = []
    for path in paths:
        if os.path.isdir(path):
            for root, _, names in os.walk(path):
                for n in sorted(names):
                    if n.lower().endswith((".html", ".htm")):
                        files.append(os.path.join(root, n))
        elif os.path.isfile(path):
            files.append(path)
    return sorted(set(files))


def site_issues(pages, site_dir=None):
    out = []
    titles = {}
    for pg in pages:
        t = pg["facts"].get("title")
        if t:
            titles.setdefault(t, []).append(pg["page"])
    dupes = {t: ps for t, ps in titles.items() if len(ps) > 1}
    if dupes:
        out.append(issue("S2", "duplicate-titles", "%d titles are used on more than one page." % len(dupes),
                         "Every page needs its own title: " +
                         "; ".join("'%s' on %d pages" % (t, len(ps)) for t, ps in list(dupes.items())[:3])))
    descs = {}
    for pg in pages:
        d = pg["facts"].get("description")
        if d:
            descs.setdefault(d, []).append(pg["page"])
    if any(len(v) > 1 for v in descs.values()):
        out.append(issue("S3", "duplicate-descriptions", "Some meta descriptions repeat across pages.",
                         "Write one per page."))
    if site_dir:
        robots = os.path.join(site_dir, "robots.txt")
        if not os.path.isfile(robots):
            out.append(issue("S2", "robots-missing", "No robots.txt.",
                             "Add one with a Sitemap: line."))
        else:
            with open(robots, encoding="utf-8", errors="replace") as fh:
                body = fh.read()
            if re.search(r"^\s*disallow:\s*/\s*$", body, re.I | re.M):
                out.append(issue("S1", "robots-blocks-all", "robots.txt disallows the whole site.",
                                 "Remove 'Disallow: /' before launch."))
            if "sitemap:" not in body.lower():
                out.append(issue("S3", "robots-no-sitemap", "robots.txt does not point to the sitemap.",
                                 "Add 'Sitemap: https://yourdomain/sitemap.xml'."))
        if not os.path.isfile(os.path.join(site_dir, "sitemap.xml")):
            out.append(issue("S2", "sitemap-missing", "No sitemap.xml.",
                             "Generate one and submit it in Search Console."))
    return out


def audit(paths, site_dir=None, local=False):
    files = collect(paths)
    pages = []
    for f in files:
        with open(f, encoding="utf-8", errors="replace") as fh:
            pages.append(audit_page(fh.read(), os.path.relpath(f), local=local))
    site = site_issues(pages, site_dir)
    avg = sum(p["score"] for p in pages) / len(pages) if pages else 0
    score = max(0, round(avg - sum(WEIGHT[i["severity"]] for i in site)))
    counts = {"S1": 0, "S2": 0, "S3": 0}
    for pg in pages:
        for i in pg["issues"]:
            counts[i["severity"]] += 1
    for i in site:
        counts[i["severity"]] += 1
    return {"score": score, "pages": pages, "site_issues": site, "counts": counts}


def compare(clone, base):
    """Codes the original fails that the clone passes (wins) and the reverse (regressions)."""
    def codes(r):
        s = set()
        for pg in r["pages"]:
            s.update(i["code"] for i in pg["issues"])
        s.update(i["code"] for i in r["site_issues"])
        return s
    c, b = codes(clone), codes(base)
    return {"score_clone": clone["score"], "score_original": base["score"],
            "delta": clone["score"] - base["score"],
            "fixed": sorted(b - c), "regressed": sorted(c - b)}


def render(result, cmp=None, markdown=False):
    h = "## " if markdown else ""
    lines = ["%sSEO score: %d/100  (S1 %d, S2 %d, S3 %d) across %d page(s)" % (
        h, result["score"], result["counts"]["S1"], result["counts"]["S2"],
        result["counts"]["S3"], len(result["pages"]))]
    if cmp:
        lines.append("vs original: %d -> %d (%+d)" % (cmp["score_original"], cmp["score_clone"], cmp["delta"]))
        if cmp["fixed"]:
            lines.append("fixed vs original: " + ", ".join(cmp["fixed"]))
        if cmp["regressed"]:
            lines.append("REGRESSED vs original: " + ", ".join(cmp["regressed"]))
    if result["site_issues"]:
        lines.append("")
        lines.append("%sSite-wide" % ("### " if markdown else ""))
        for i in result["site_issues"]:
            lines.append("- [%s] %s  Fix: %s" % (i["severity"], i["message"], i["fix"]))
    for pg in sorted(result["pages"], key=lambda p: p["score"]):
        lines.append("")
        lines.append("%s%s  %d/100" % ("### " if markdown else "", pg["page"], pg["score"]))
        for i in sorted(pg["issues"], key=lambda i: i["severity"]):
            lines.append("- [%s] %s  Fix: %s" % (i["severity"], i["message"], i["fix"]))
        if not pg["issues"]:
            lines.append("- no issues found")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Static SEO audit of saved HTML pages.")
    ap.add_argument("paths", nargs="+", help="HTML files or folders")
    ap.add_argument("--site", help="site root to check robots.txt and sitemap.xml")
    ap.add_argument("--baseline", help="folder of the original's pages, to compare against")
    ap.add_argument("--local", action="store_true", help="local business checks")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--markdown", action="store_true")
    ap.add_argument("--fail-under", type=int, default=None)
    args = ap.parse_args(argv)

    result = audit(args.paths, args.site, args.local)
    if not result["pages"]:
        print("seo: no .html files found in %s" % ", ".join(args.paths), file=sys.stderr)
        return 2
    cmp = None
    if args.baseline:
        base = audit([args.baseline], None, args.local)
        if base["pages"]:
            cmp = compare(result, base)
    if args.json:
        payload = dict(result)
        if cmp:
            payload["compare"] = cmp
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(render(result, cmp, args.markdown))
    if args.fail_under is not None and result["score"] < args.fail_under:
        print("seo: score %d is under %d" % (result["score"], args.fail_under), file=sys.stderr)
        return 1
    if result["counts"]["S1"] and args.fail_under is not None:
        print("seo: %d S1 issue(s) open" % result["counts"]["S1"], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
