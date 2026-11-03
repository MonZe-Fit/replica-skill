---
name: replica-seo
description: >-
  SEO and performance audit for an app or website: on-page, technical,
  structured data and local SEO checks on saved HTML, plus a Core Web Vitals
  budget from Lighthouse reports, scored and ranked by severity. Runs twice:
  as a baseline on the original (to find where it is weak and you can win)
  and as a gate on the clone before deploy (it must match or beat the
  original). Two standard-library Python tools. Use when the user says "SEO
  audit", "check performance", "page speed", "Core Web Vitals", "why is it
  slow", "will it rank", "improve the current site", "audit my site", or
  after /replica-recon and before /replica-deploy.
---

# replica-seo

Two tools in this folder, both standard-library Python, no installs, no network:

```bash
python3 seo.py replica/seo/original/ --local --markdown > replica/seo/original.md
python3 seo.py out/ --site out/ --baseline replica/seo/original/ --fail-under 85
python3 perf.py replica/seo/lh/clone-*.json --baseline replica/seo/lh/original-*.json --gate
```

## Why this skill exists

A clone that does the same job but loads slower and ranks lower has no reason
to exist. Search and speed are where most small sites lose quietly, and they
are the cheapest place to beat an incumbent: the original's weaknesses here
are measurable, public, and fixable in your rebuild from day one.

It also works when there is no "original" to beat: point it at the user's own
site (or a client's, with permission) to get the improvement plan for the
current version. In that mode skip `/replica-brand` and the sweep: the brand
is theirs to keep.

## The rules

- **Same reading rules as the rest of the pack.** Public pages only, the way a
  person sees them. No crawling against robots.txt or a site's terms, no
  pages behind a login that is not the user's own.
- **Measure, do not guess.** Every claim in the report comes from a tool
  output, a Lighthouse report or a page you saved. Label anything else as
  inference. No invented search volumes, rankings or traffic numbers. If you
  have no keyword tool, say so and list the queries as hypotheses.
- **Mobile first.** Google indexes the mobile version. Mobile Lighthouse
  numbers are the ones that gate.

## Mode A: baseline on the original (after /replica-recon)

### Step 1: save the pages

From `replica/recon.md`, pick the pages that bring traffic: home, each main
service or product page, pricing, contact, the blog index and one post.
Save each as HTML into `replica/seo/original/` (browser "Save page as, HTML
only", or view-source copy). If the site is a JavaScript app and the saved
HTML is nearly empty, save the rendered DOM too (DevTools > Elements > copy
outerHTML) and note it: an empty server response is itself an S2 finding,
because crawlers may not wait for the render.

### Step 2: SEO audit

```bash
python3 seo.py replica/seo/original/ --local --markdown > replica/seo/original.md
python3 seo.py replica/seo/original/ --local --json > replica/seo/original.json
```

Use `--local` for any business with a physical location or service area
(restaurants, clinics, consultancies, hotels). It checks LocalBusiness
structured data, name/address/phone and tap-to-call.

What it checks, by severity:

| severity | examples |
| --- | --- |
| S1 blocks indexing or ranking | noindex, no title, no viewport, several canonicals, robots.txt blocks all |
| S2 costs rankings or clicks | no meta description, no h1, thin content, images without alt or size, render-blocking JS, missing canonical, lang, sitemap, invalid JSON-LD, duplicate titles |
| S3 polish | title or description length, heading skips, Open Graph, favicon, lazy loading, WebP/AVIF, noopener |

### Step 3: performance

Run Lighthouse on the same pages, **mobile**, three times each, keep the
median report:

```bash
npx lighthouse https://original.example/ --output=json --output-path=replica/seo/lh/original-home-mobile.json --quiet
npx lighthouse https://original.example/ --preset=desktop --output=json --output-path=replica/seo/lh/original-home-desktop.json --quiet
python3 perf.py replica/seo/lh/original-*.json --markdown > replica/seo/original-perf.md
```

No Node? Use pagespeed.web.dev in the browser and download the JSON, or Chrome
DevTools > Lighthouse > Save as JSON. A PageSpeed Insights report also carries
**field data** (real Chrome users, 28 days): when present it wins over lab
numbers, and it is the only source for INP.

### Step 4: keywords and intent

For each saved page write one line in `replica/seo/keywords.md`: the query it
is trying to win, the intent (find, compare, buy, visit), and whether the
title, h1 and first paragraph actually say it. Sources, in order of trust:
the user's own Search Console (if it is their site), a keyword tool the user
has, then the search results page itself read by hand. Mark which.

### Step 5: the opportunity list

`replica/seo/baseline.md`: SEO score, mobile performance score, each Core Web
Vital with its rating, and the original's weaknesses ranked by what they cost.
Each line tagged **[evidence]** (tool or report output) or **[inference]**
(your judgment). Hand the top items to:

- `/replica-architect`: choose a stack that renders HTML on the server
  (static or SSR) if the original's weakness is a client-rendered app.
- `/replica-entrepreneur`: speed and findability are positioning ("loads in
  1.5 s on a phone where the original takes 6").
- `/replica-launch`: titles, descriptions and headings from `keywords.md`.

## Mode B: gate on the clone (after /replica-test, before /replica-deploy)

Build the production version, then audit its HTML output (`out/`, `dist/`,
`.next/server/app/` prerendered pages, or pages saved from a preview URL):

```bash
python3 seo.py out/ --site out/ --local --baseline replica/seo/original/ --fail-under 85
npx lighthouse https://preview.yourapp.dev/ --output=json --output-path=replica/seo/lh/clone-home-mobile.json --quiet
python3 perf.py replica/seo/lh/clone-*.json --baseline replica/seo/lh/original-*.json --gate --markdown > replica/seo/gate.md
```

`seo.py --baseline` lists the issues you **fixed** compared to the original
and any you **regressed**. `perf.py --baseline` puts both apps side by side,
metric by metric.

### The gate

| check | passes when |
| --- | --- |
| SEO score | 85 or more, no S1 open |
| vs original | SEO score and mobile performance score equal or higher; no regressed codes |
| Core Web Vitals (mobile) | no metric rated poor; LCP and CLS good is the target |
| page weight | under the budget (default 1600 KB, `--budget-kb` to change) |
| site files | robots.txt (not blocking), sitemap.xml, canonical host chosen |

Not passing means not shippable. Fix with `/replica-build`, then run again.

## Common fixes, fastest first

1. Hero image: compress, serve WebP/AVIF, set width/height, `fetchpriority="high"`, never lazy.
2. Scripts: `defer` everything in `<head>`, drop unused third parties (chat widgets, old pixels).
3. Fonts: self-host, `font-display: swap`, preload the one used above the fold.
4. One unique title, description and h1 per page, from `keywords.md`.
5. LocalBusiness JSON-LD with the exact same name, address and phone as the
   Google Business Profile.
6. Server-render or prerender pages that must rank.

## Output

`replica/seo/baseline.md` (mode A) or `replica/seo/gate.md` (mode B), the
JSON outputs, and the top five fixes. Then `/replica-architect` after a
baseline, or `/replica-deploy` once the gate passes.
