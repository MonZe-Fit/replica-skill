import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load

seo = load("replica-seo", "seo")
perf = load("replica-seo", "perf")

GOOD = """<!doctype html><html lang="es-MX"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nutricion funcional en San Jose del Cabo | Nu Fit</title>
<meta name="description" content="Planes de nutricion antiinflamatoria y circadiana en Los Cabos, con seguimiento y menus semanales.">
<link rel="canonical" href="https://example.mx/">
<link rel="icon" href="/favicon.ico">
<meta property="og:title" content="Nu Fit"><meta property="og:description" content="x">
<meta property="og:image" content="https://example.mx/og.jpg">
<script src="/app.js" defer></script>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"LocalBusiness",
"name":"Nu Fit","telephone":"+52 624 000 0000","address":{"@type":"PostalAddress","streetAddress":"Calle 1"},
"openingHoursSpecification":[{"@type":"OpeningHoursSpecification","dayOfWeek":"Monday"}]}</script>
</head><body><h1>Nutricion funcional</h1><h2>Servicios</h2>
<p>%s</p><img src="/a.webp" alt="plato" width="400" height="300">
<a href="/contacto">Contacto</a> <a href="tel:+526240000000">Llamar</a></body></html>""" % ("palabra " * 300)

BAD = """<html><head><meta name="robots" content="noindex">
<script src="/jquery.js"></script></head>
<body><h1>A</h1><h1>B</h1><h4>C</h4><img src="/a.jpg"><img src="/b.png"><img src="/c.jpg">
</body></html>"""


def lighthouse(score, lcp, cls, tbt, weight_kb, field=None):
    lhr = {"finalDisplayedUrl": "https://example.mx/",
           "configSettings": {"formFactor": "mobile"},
           "categories": {"performance": {"score": score}},
           "audits": {
               "largest-contentful-paint": {"numericValue": lcp},
               "cumulative-layout-shift": {"numericValue": cls},
               "total-blocking-time": {"numericValue": tbt},
               "first-contentful-paint": {"numericValue": 1500},
               "speed-index": {"numericValue": 3000},
               "total-byte-weight": {"numericValue": weight_kb * 1024},
               "network-requests": {"details": {"items": [{}] * 42}},
               "render-blocking-resources": {"title": "Eliminate render-blocking resources",
                                             "details": {"type": "opportunity", "overallSavingsMs": 900}},
               "tiny": {"title": "Tiny", "details": {"type": "opportunity", "overallSavingsMs": 10}},
           }}
    if field:
        return {"lighthouseResult": lhr, "loadingExperience": {"metrics": field}}
    return lhr


class Seo(unittest.TestCase):
    def test_clean_page_scores_high(self):
        r = seo.audit_page(GOOD, "good.html", local=True)
        codes = [i["code"] for i in r["issues"]]
        self.assertGreaterEqual(r["score"], 95, codes)
        self.assertNotIn("local-schema", codes)
        self.assertIn("localbusiness", r["facts"]["jsonld_types"])

    def test_bad_page_finds_the_blockers(self):
        r = seo.audit_page(BAD, "bad.html")
        codes = {i["code"]: i["severity"] for i in r["issues"]}
        for code in ("noindex", "title-missing", "viewport-missing"):
            self.assertEqual(codes.get(code), "S1", code)
        for code in ("img-alt", "img-dimensions", "render-blocking-js", "lang-missing", "thin-content"):
            self.assertIn(code, codes)
        self.assertIn("h1-multiple", codes)
        self.assertIn("heading-skip", codes)
        self.assertLess(r["score"], 40)

    def test_invalid_jsonld_is_reported(self):
        html = GOOD.replace('"name":"Nu Fit",', '"name":"Nu Fit",,')
        codes = [i["code"] for i in seo.audit_page(html, "x", local=True)["issues"]]
        self.assertIn("jsonld-invalid", codes)

    def test_local_checks_only_when_asked(self):
        html = BAD
        self.assertNotIn("local-schema", [i["code"] for i in seo.audit_page(html)["issues"]])
        self.assertIn("local-schema", [i["code"] for i in seo.audit_page(html, local=True)["issues"]])

    def test_site_files_duplicates_and_compare(self):
        with tempfile.TemporaryDirectory() as clone, tempfile.TemporaryDirectory() as orig:
            for n in ("index.html", "about.html"):
                with open(os.path.join(clone, n), "w") as fh:
                    fh.write(GOOD)
            with open(os.path.join(orig, "index.html"), "w") as fh:
                fh.write(BAD)
            with open(os.path.join(clone, "robots.txt"), "w") as fh:
                fh.write("User-agent: *\nDisallow: /\n")
            r = seo.audit([clone], site_dir=clone)
            site = {i["code"] for i in r["site_issues"]}
            self.assertIn("duplicate-titles", site)
            self.assertIn("robots-blocks-all", site)
            self.assertIn("sitemap-missing", site)
            cmp = seo.compare(seo.audit([clone]), seo.audit([orig]))
            self.assertIn("noindex", cmp["fixed"])
            self.assertGreater(cmp["delta"], 0)

    def test_cli_gate_exit_codes(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "index.html"), "w") as fh:
                fh.write(BAD)
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(seo.main([d, "--fail-under", "85"]), 1)
                self.assertEqual(seo.main([d]), 0)
                self.assertEqual(seo.main([os.path.join(d, "none")]), 2)


class Perf(unittest.TestCase):
    def write(self, d, name, data):
        path = os.path.join(d, name)
        with open(path, "w") as fh:
            json.dump(data, fh)
        return path

    def test_ratings_and_opportunities(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.write(d, "c.json", lighthouse(0.93, 2100, 0.05, 150, 900))
            r = perf.read(p)
            self.assertEqual(r["score"], 93)
            self.assertEqual(r["lab"]["LCP"]["rating"], "good")
            self.assertEqual(r["requests"], 42)
            self.assertEqual([o["id"] for o in r["opportunities"]], ["render-blocking-resources"])
            self.assertEqual(r["fails"], [])

    def test_poor_metrics_and_budget_fail_the_gate(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.write(d, "c.json", lighthouse(0.31, 6200, 0.4, 900, 4000))
            r = perf.read(p)
            self.assertTrue(any("LCP" in f for f in r["fails"]))
            self.assertTrue(any("weight" in f for f in r["fails"]))
            with redirect_stdout(io.StringIO()):
                self.assertEqual(perf.main([p, "--gate"]), 1)
                self.assertEqual(perf.main([p]), 0)

    def test_field_data_wins_and_gives_inp(self):
        field = {"LARGEST_CONTENTFUL_PAINT_MS": {"percentile": 2000},
                 "CUMULATIVE_LAYOUT_SHIFT_SCORE": {"percentile": 5},
                 "INTERACTION_TO_NEXT_PAINT": {"percentile": 650}}
        with tempfile.TemporaryDirectory() as d:
            p = self.write(d, "psi.json", lighthouse(0.5, 5000, 0.3, 100, 800, field))
            r = perf.read(p)
            self.assertAlmostEqual(r["field"]["CLS"]["value"], 0.05)
            self.assertEqual(r["field"]["INP"]["rating"], "poor")
            self.assertFalse(any(f.startswith("LCP") for f in r["fails"]))  # field LCP is good
            self.assertTrue(any(f.startswith("INP") for f in r["fails"]))

    def test_baseline_comparison_renders(self):
        with tempfile.TemporaryDirectory() as d:
            c = self.write(d, "clone.json", lighthouse(0.92, 2000, 0.02, 100, 700))
            o = self.write(d, "orig.json", lighthouse(0.45, 5500, 0.2, 700, 3100))
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(perf.main([c, "--baseline", o, "--markdown"]), 0)
            text = buf.getvalue()
            self.assertIn("original 45, +47", text)
            self.assertIn("| LCP (lab) | 2.0 s | good | 5.5 s |", text)

    def test_not_a_report(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.write(d, "x.json", {"hello": 1})
            with redirect_stderr(io.StringIO()):
                self.assertEqual(perf.main([p]), 2)


if __name__ == "__main__":
    unittest.main()
