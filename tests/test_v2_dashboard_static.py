"""Static checks of the dashboard: safe DOM usage, wiring, theming, accessibility."""
import re
import shutil
import subprocess
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"


class DashboardStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (STATIC / "index.html").read_text()
        cls.js = (STATIC / "app.js").read_text()
        cls.css = (STATIC / "app.css").read_text()

    def test_no_html_injection_sinks_or_inline_code(self):
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
            self.assertNotIn(sink, self.js)
        self.assertNotRegex(self.html, r"\son[a-z]+=")  # no inline handlers (CSP forbids them anyway)
        self.assertNotRegex(self.html, r"<script>(?!</script>)")
        self.assertNotIn("style=", self.html)
        self.assertNotRegex(self.html, r"https?://(?!www\.w3\.org)")  # no external assets

    def test_every_element_id_used_by_script_exists(self):
        ids = set(re.findall(r'\bid="([^"]+)"', self.html))
        used = set(re.findall(r'\$\("([^"]+)"\)', self.js))
        dynamic = {"tab-" + t for t in ("requests", "history", "activity", "memory")} | \
                  {"panel-" + t for t in ("requests", "history", "activity", "memory")}
        self.assertFalse((used - ids), f"missing ids: {used - ids}")
        self.assertTrue(dynamic <= ids)

    def test_dark_mode_and_responsive_and_reduced_motion(self):
        self.assertIn('content="light dark"', self.html)
        self.assertIn("prefers-color-scheme:dark", self.css)
        self.assertIn('[data-theme="dark"]', self.css)
        self.assertIn("@media (max-width:520px)", self.css)
        self.assertIn("prefers-reduced-motion", self.css)

    def test_accessibility_basics(self):
        self.assertIn('role="tablist"', self.html)
        self.assertEqual(self.html.count('role="tab"'), 4)
        self.assertIn('aria-live="polite"', self.html)
        self.assertIn('class="skip"', self.html)
        for match in re.finditer(r'<(input|select|textarea)[^>]*id="([^"]+)"', self.html):
            control_id = match.group(2)
            labelled = f'for="{control_id}"' in self.html or re.search(
                r"<label[^>]*>[^<]*<(input|select)[^>]*id=\"" + re.escape(control_id), self.html)
            self.assertTrue(labelled, f"{control_id} has no label")

    def test_dashboard_calls_only_known_api_routes(self):
        routes = set(re.findall(r'api\("([a-z/]+)', self.js)) | set(re.findall(r'mutate\("([a-z]+)"', self.js))
        known = {"state", "status", "history", "audit", "login", "logout", "propose", "approve", "deny",
                 "execute", "pause", "memory/workspaces", "memory/projects", "memory/records",
                 "memory/records/delete"}
        self.assertTrue(routes <= known, routes - known)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_script_parses(self):
        subprocess.run(["node", "--check", str(STATIC / "app.js")], check=True, capture_output=True)


if __name__ == "__main__":
    unittest.main()
