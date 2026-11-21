"""v2.1 HUD theme: assets are served safely, stay self-contained, and keep accessibility guarantees."""
import re
import unittest
from pathlib import Path

import server
from v2_helpers import LiveServer

STATIC = Path(__file__).resolve().parent.parent / "static"
FILES = {name: (STATIC / name).read_text() for name in ("index.html", "app.css", "app.js", "icon.svg")}


class HudStaticTests(unittest.TestCase):
    html, css, js = FILES["index.html"], FILES["app.css"], FILES["app.js"]

    def test_no_external_urls_imports_or_fonts_in_any_static_file(self):
        for name, text in FILES.items():
            with self.subTest(file=name):
                # Only the SVG XML namespace may appear; it is an identifier, not a fetch.
                self.assertNotRegex(text, r"https?://(?!www\.w3\.org/2000/svg)")
                self.assertNotRegex(text, r"(?<![:\w-])//[a-z0-9.-]+\.[a-z]{2,}/")
                self.assertNotIn("@import", text)
                self.assertNotIn("@font-face", text)
        # CSS url() may only point at in-document fragments (SVG gradients), never files.
        for ref in re.findall(r"url\(([^)]*)\)", self.css + self.html):
            self.assertTrue(ref.strip("'\" ").startswith("#"), ref)

    def test_reduced_motion_turns_off_rotation_scanlines_and_boot(self):
        block = self.css[self.css.index("@media (prefers-reduced-motion:reduce)"):]
        block = block[:block.index("\n}\n")]
        for needle in (".spin", ".boost", ".telemetry>span", ".hud-frame::after", "body::before", ".boot{display:none!important}",
                       "animation:none!important"):
            self.assertIn(needle, block)
        self.assertIn('matchMedia("(prefers-reduced-motion: reduce)")', self.js)
        self.assertRegex(self.js, r"if \(reducedMotion\.matches\) return;")

    def test_boot_sequence_is_short_and_skippable(self):
        timeout = int(re.search(r"setTimeout\(endBoot, (\d+)\)", self.js).group(1))
        self.assertLessEqual(timeout, 2000)
        self.assertIn('id="boot-skip"', self.html)
        self.assertIn('$("boot-skip").addEventListener("click", endBoot)', self.js)
        self.assertIn('e.key === "Escape"', self.js)

    def test_dark_hud_is_the_default_theme_and_light_remains(self):
        self.assertIn('const THEMES = ["dark", "light", "system"]', self.js)
        self.assertIn(': "dark"; } catch { return "dark"; }', self.js)
        root = self.css[self.css.index(":root,:root[data-theme=\"dark\"]{"):]
        self.assertIn("color-scheme:dark", root[:root.index("}")])
        self.assertIn(':root[data-theme="light"]{', self.css)

    def test_decorative_graphics_are_hidden_from_assistive_tech(self):
        for svg in re.findall(r"<svg\b[^>]*>", self.html):
            self.assertIn('aria-hidden="true"', svg)
        for cls in ("telemetry", "hud-frame", "core-label", "boot-visual"):
            tag = re.search(r'<[a-z]+[^>]*class="[^"]*\b' + cls + r'\b[^"]*"[^>]*>', self.html).group(0)
            self.assertIn('aria-hidden="true"', tag, cls)
        # The unlock emblem wrapper hides its icons.
        self.assertRegex(self.html, r'class="unlock-emblem" aria-hidden="true"')

    def test_core_states_and_hidden_tab_pause(self):
        for state in ("alert", "busy", "paused", "offline"):
            self.assertIn(f'[data-core="{state}"]', self.css)
        self.assertIn(".is-hidden *", self.css)
        self.assertIn("animation-play-state:paused!important", self.css)
        self.assertIn('document.documentElement.classList.toggle("is-hidden", document.hidden)', self.js)

    def test_phone_layout_rules_present(self):
        self.assertIn("@media (max-width:900px)", self.css)
        self.assertIn("@media (max-width:520px)", self.css)
        self.assertRegex(self.css, r"\.core\{width:min\(250px,68vw\)\}")

    def test_no_film_studio_marks(self):
        for name, text in FILES.items():
            for word in ("marvel", "stark", "avengers", "iron man", "ironman", "disney"):
                self.assertNotIn(word, text.lower(), f"{word} in {name}")
        self.assertIn("Jarvis Companion", self.html)

    def test_hud_text_uses_textcontent_only(self):
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "setAttribute(\"style\"", ".style."):
            self.assertNotIn(sink, self.js)


class HudServedTests(unittest.TestCase):
    def setUp(self):
        self.s = LiveServer()

    def tearDown(self):
        self.s.close()

    def test_static_assets_served_with_csp_and_types(self):
        expected = {"/": "text/html", "/app.css": "text/css", "/app.js": "text/javascript", "/icon.svg": "image/svg+xml"}
        for path, ctype in expected.items():
            with self.subTest(path=path):
                status, body, headers = self.s.request("GET", path)
                self.assertEqual(status, 200)
                self.assertIn(ctype, headers["Content-Type"])
                csp = headers["Content-Security-Policy"]
                self.assertIn("default-src 'none'", csp)
                self.assertIn("script-src 'self'", csp)
                self.assertIn("style-src 'self'", csp)
                self.assertNotIn("unsafe-inline", csp)
        status, body, _ = self.s.request("GET", "/")
        self.assertIn(b'class="core-svg"', body)
        self.assertNotIn(b"style=", body)

    def test_version_is_2_1(self):
        self.assertEqual(server.VERSION, "2.1.0")
        self.s.login()
        self.assertEqual(self.s.request("GET", "/api/status")[1]["version"], "2.1.0")


if __name__ == "__main__":
    unittest.main()
