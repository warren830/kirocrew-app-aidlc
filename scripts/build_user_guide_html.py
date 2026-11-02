"""Render the user guide as one self-contained HTML file, screenshots inlined.

    python scripts/build_user_guide_html.py                     # docs/user-guide.zh-CN.md -> docs/user-guide.zh-CN.html
    python scripts/build_user_guide_html.py docs/user-guide.md  # the English guide

Needs markdown-it-py; the Python bundled with the KiroCrew desktop app has it. Links to other files in this
repository point at GitHub, because a single HTML file opened on its own has no repository beside it.
"""

from __future__ import annotations

import base64
import html
import re
import sys
from pathlib import Path

from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parent.parent
REPO_URL = "https://github.com/warren830/kirocrew-app-aidlc/blob/v2/"

CSS = """
:root { --fg:#1f2328; --muted:#59636e; --line:#d1d9e0; --soft:#f6f8fa; --accent:#7c3aed; --warn:#9a6700; }
* { box-sizing: border-box; }
body { margin: 0; color: var(--fg); background: #fff; font: 16px/1.7 -apple-system, BlinkMacSystemFont,
  "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Segoe UI", Helvetica, Arial, sans-serif; }
main { max-width: 980px; margin: 0 auto; padding: 40px 28px 96px; }
h1 { font-size: 2em; border-bottom: 1px solid var(--line); padding-bottom: .3em; }
h2 { font-size: 1.5em; margin-top: 2.2em; border-bottom: 1px solid var(--line); padding-bottom: .3em; }
h3 { font-size: 1.2em; margin-top: 1.8em; }
a { color: #0969da; text-decoration: none; } a:hover { text-decoration: underline; }
code { font: .88em/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; background: var(--soft);
  padding: .15em .4em; border-radius: 6px; }
pre { background: var(--soft); border: 1px solid var(--line); border-radius: 8px; padding: 14px 16px; overflow: auto; }
pre code { background: none; padding: 0; font-size: .86em; }
table { border-collapse: collapse; width: 100%; margin: 1em 0; font-size: .94em; display: block; overflow-x: auto; }
th, td { border: 1px solid var(--line); padding: 7px 12px; text-align: left; vertical-align: top; }
th { background: var(--soft); }
blockquote { margin: 1em 0; padding: .2em 1em; color: var(--muted); border-left: 4px solid var(--line); }
.callout { margin: 1.2em 0; padding: 12px 18px; border-left: 4px solid var(--accent); background: #f5f0ff; border-radius: 6px; }
.callout-title { font-weight: 600; color: var(--accent); margin: .2em 0 .4em; }
img { max-width: 100%; height: auto; border: 1px solid var(--line); border-radius: 8px;
  box-shadow: 0 2px 10px rgba(0,0,0,.06); cursor: zoom-in; vertical-align: top; }
p > img + img { margin-left: 16px; }
#zoom { position: fixed; inset: 0; background: rgba(15,15,20,.85); display: none; align-items: center;
  justify-content: center; padding: 24px; cursor: zoom-out; z-index: 10; }
#zoom img { max-width: 100%; max-height: 100%; cursor: zoom-out; border: 0; }
"""

CALLOUT_TITLES = {
    "zh-CN": {"IMPORTANT": "重要", "NOTE": "说明", "WARNING": "警告", "TIP": "提示"},
    "en": {"IMPORTANT": "Important", "NOTE": "Note", "WARNING": "Warning", "TIP": "Tip"},
}

ZOOM_JS = """
const zoom = document.getElementById('zoom'), big = zoom.querySelector('img');
document.querySelectorAll('main img').forEach((img) => img.addEventListener('click', () => {
  big.src = img.src; big.alt = img.alt; zoom.style.display = 'flex';
}));
zoom.addEventListener('click', () => { zoom.style.display = 'none'; });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') zoom.style.display = 'none'; });
"""


def inline_image(src: str, base: Path) -> str:
    path = (base / src).resolve()
    if not path.is_file():
        raise SystemExit(f"image not found: {src} (resolved to {path})")
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def repo_link(href: str, base: Path) -> str:
    """A link to another file in the repository, as a GitHub URL; anchors and web links unchanged."""
    if href.startswith(("http://", "https://", "mailto:", "#")):
        return href
    target, _, frag = href.partition("#")
    rel = (base / target).resolve().relative_to(ROOT).as_posix()
    return REPO_URL + rel + (f"#{frag}" if frag else "")


def render(source: Path) -> str:
    md = MarkdownIt("commonmark", {"html": True}).enable("table")
    tokens = md.parse(source.read_text("utf-8"))
    base = source.parent
    lang = "zh-CN" if source.name.endswith(".zh-CN.md") else "en"
    for token in tokens:
        for child in token.children or ():
            if child.type == "image":
                child.attrSet("src", inline_image(child.attrGet("src"), base))
            elif child.type == "link_open":
                child.attrSet("href", repo_link(child.attrGet("href"), base))
    body = md.renderer.render(tokens, md.options, {})
    # GitHub's "> [!IMPORTANT]" alert, which commonmark renders as a plain blockquote.
    body = re.sub(
        r"<blockquote>\s*<p>\[!(IMPORTANT|NOTE|WARNING|TIP)\]\s*",
        lambda m: f'<blockquote class="callout"><p class="callout-title">{CALLOUT_TITLES[lang][m.group(1)]}</p><p>',
        body,
    )
    title = next((t.content for i, t in enumerate(tokens) if t.type == "inline" and tokens[i - 1].tag == "h1"), "User guide")
    return (
        f'<!doctype html>\n<html lang="{lang}">\n<head>\n<meta charset="utf-8">\n'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html.escape(title)}</title>\n<style>{CSS}</style>\n</head>\n<body>\n<main>\n{body}</main>\n"
        f'<div id="zoom"><img alt=""></div>\n<script>{ZOOM_JS}</script>\n</body>\n</html>\n'
    )


def main() -> None:
    source = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "docs" / "user-guide.zh-CN.md"
    out = source.with_suffix(".html")
    out.write_text(render(source), "utf-8")
    print(f"{out.relative_to(ROOT)}  {out.stat().st_size / 1_000_000:.1f} MB")


if __name__ == "__main__":
    main()
