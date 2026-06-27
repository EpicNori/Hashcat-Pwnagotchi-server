import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PALETTE_DOC = ROOT / "docs" / "color-palette.md"
STATIC_ROOT = ROOT / "app" / "static"
TEMPLATE_ROOT = ROOT / "app" / "templates"

HEX_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")
RGB_RE = re.compile(r"rgba?\([^)]*\)", re.IGNORECASE)
NAMED_PROPERTY_RE = re.compile(
    r":\s*(red|blue|green|yellow|orange|purple|black|white)\s*(?:;|!important)",
    re.IGNORECASE,
)
CODE_SPAN_RE = re.compile(r"`([^`]+)`")
DOC_COLOR_RE = re.compile(
    r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|\b(?:red|blue|green|yellow|orange|purple|black|white)\b",
    re.IGNORECASE,
)
RAW_ANSI_RE = re.compile(r"\\033\[[0-9;]*m|\\e\[[0-9;]*m")
CLI_ANSI_ALLOWLIST = {
    Path("bash/cli_theme.sh"),
    Path("install.sh"),
    Path("update.sh"),
}


def normalize_color(value):
    return re.sub(r"\s+", "", value.strip().lower())


def line_number(text, position):
    return text.count("\n", 0, position) + 1


def approved_colors():
    text = PALETTE_DOC.read_text(encoding="utf-8")
    colors = set()
    for code_span in CODE_SPAN_RE.findall(text):
        for match in DOC_COLOR_RE.finditer(code_span):
            colors.add(normalize_color(match.group(0)))
    return colors


def frontend_files():
    for root, suffixes in (
        (STATIC_ROOT, {".css", ".svg"}),
        (TEMPLATE_ROOT, {".html"}),
    ):
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in suffixes:
                yield path


def shell_files():
    candidates = [ROOT / "install.sh", ROOT / "update.sh"]
    candidates.extend(path for path in (ROOT / "bash").iterdir() if path.is_file())
    return candidates


class ColorPaletteTests(unittest.TestCase):
    def test_frontend_color_literals_are_documented_palette_entries(self):
        palette = approved_colors()
        violations = []

        for path in frontend_files():
            text = path.read_text(encoding="utf-8")
            matches = []
            matches.extend(HEX_RE.finditer(text))
            matches.extend(RGB_RE.finditer(text))
            matches.extend(NAMED_PROPERTY_RE.finditer(text))

            for match in matches:
                literal = match.group(1) if match.re is NAMED_PROPERTY_RE else match.group(0)
                if normalize_color(literal) not in palette:
                    rel_path = path.relative_to(ROOT)
                    violations.append(f"{rel_path}:{line_number(text, match.start())}: {literal}")

        self.assertEqual(
            [],
            violations,
            "Add new colors to docs/color-palette.md and expose them as semantic tokens.",
        )

    def test_cli_ansi_colors_only_live_in_theme_entrypoints(self):
        violations = []

        for path in shell_files():
            text = path.read_text(encoding="utf-8")
            rel_path = path.relative_to(ROOT)
            if rel_path in CLI_ANSI_ALLOWLIST:
                continue

            for match in RAW_ANSI_RE.finditer(text):
                violations.append(f"{rel_path}:{line_number(text, match.start())}: {match.group(0)}")

        self.assertEqual(
            [],
            violations,
            "CLI scripts must use bash/cli_theme.sh instead of defining raw ANSI colors.",
        )


if __name__ == "__main__":
    unittest.main()
