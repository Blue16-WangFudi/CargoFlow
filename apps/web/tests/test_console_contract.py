from __future__ import annotations

import re
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[3]
WEB_DIR = ROOT_DIR / "apps" / "web"


class ConsoleHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.scripts: list[str] = []
        self.stylesheets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {name: value for name, value in attrs}
        element_id = attr_map.get("id")
        if element_id:
            self.ids.add(element_id)
        if tag == "script" and attr_map.get("src"):
            self.scripts.append(attr_map["src"] or "")
        if tag == "link" and attr_map.get("rel") == "stylesheet":
            self.stylesheets.append(attr_map.get("href") or "")


def parse_console_html() -> ConsoleHtmlParser:
    parser = ConsoleHtmlParser()
    parser.feed((WEB_DIR / "index.html").read_text(encoding="utf-8"))
    return parser


class FrontendConsoleContractTests(unittest.TestCase):
    def test_index_links_required_assets(self) -> None:
        parser = parse_console_html()

        self.assertIn("./styles.css", parser.stylesheets)
        self.assertIn("./app.js", parser.scripts)

    def test_app_targets_existing_dom_ids(self) -> None:
        parser = parse_console_html()
        app_js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
        referenced_ids = set(re.findall(r'\$\("([^"]+)"\)', app_js))

        self.assertGreater(len(referenced_ids), 0)
        self.assertFalse(referenced_ids - parser.ids)

    def test_console_exposes_expected_status_fields(self) -> None:
        parser = parse_console_html()

        required_ids = {
            "service-status",
            "status-label",
            "role-select",
            "dispatch-view",
            "alert-handling-view",
            "warehouse-view",
            "shipper-view",
            "driver-view",
            "admin-logs-view",
        }
        self.assertTrue(required_ids.issubset(parser.ids))

    def test_app_calls_expected_api_routes(self) -> None:
        app_js = (WEB_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn("/api/shipments/demo", app_js)
        self.assertIn("/api/vehicles", app_js)
        self.assertIn("/api/alerts", app_js)


if __name__ == "__main__":
    unittest.main()
