#!/usr/bin/env python3
"""Generate a concise static rules page from RULES.md."""

from __future__ import annotations

import argparse
import html
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

SECTION_RE = re.compile(r"^==\s*(.+?)\s*==$")
OPTIONAL_MODULE_RE = re.compile(r"^—\s*(.+?)\s*—$")
DICE_TOKEN_RE = re.compile(r"\b(\d*)d(4|6|8|10|12|20)\b")


@dataclass
class Section:
    title: str
    lines: list[str]


@dataclass
class Module:
    title: str
    lines: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate rules HTML from markdown source.")
    parser.add_argument("--input", default="RULES.md", help="Input rules markdown file.")
    parser.add_argument("--output", default="docs/index.html", help="Output HTML file.")
    return parser.parse_args()


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "section"


def parse_sections(text: str) -> list[Section]:
    sections: list[Section] = []
    current_title: str | None = None
    current_lines: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        match = SECTION_RE.match(line)
        if match:
            if current_title is not None:
                sections.append(Section(current_title, current_lines))
            current_title = match.group(1)
            current_lines = []
            continue
        if current_title is not None:
            current_lines.append(line)

    if current_title is not None:
        sections.append(Section(current_title, current_lines))

    return sections


def split_optional_modules(section: Section) -> list[Module]:
    modules: list[Module] = []
    current_title: str | None = None
    current_lines: list[str] = []

    for raw_line in section.lines:
        line = raw_line.strip()
        mod = OPTIONAL_MODULE_RE.match(line)
        if mod:
            if current_title is not None:
                modules.append(Module(current_title, current_lines))
            current_title = mod.group(1)
            current_lines = []
            continue

        if current_title is not None:
            current_lines.append(raw_line)

    if current_title is not None:
        modules.append(Module(current_title, current_lines))

    return modules


def compact_example_text(text: str, max_items: int = 3) -> str:
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) <= max_items:
        return text
    return ", ".join(parts[:max_items]) + ", etc."


def compress_line(text: str) -> str:
    text = " ".join(text.split())
    if not text:
        return ""

    lowered = text.lower()
    if lowered.startswith("for example"):
        return compact_example_text(text, max_items=2)
    if lowered.startswith("example:"):
        prefix, _, rest = text.partition(":")
        return f"{prefix}: {compact_example_text(rest.strip(), max_items=4)}"

    return text


def apply_dice_markup(escaped_text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        token = match.group(0)
        die = f"d{match.group(2)}"
        safe_token = html.escape(token)
        safe_die = html.escape(die)
        return (
            '<span class="dice-token" aria-label="'
            + safe_token
            + '"><span class="dice-glyph">'
            + safe_die
            + '</span><span class="dice-text">'
            + safe_token
            + "</span></span>"
        )

    return DICE_TOKEN_RE.sub(repl, escaped_text)


def render_text(text: str) -> str:
    escaped = html.escape(compress_line(text))
    return apply_dice_markup(escaped)


def render_blocks(lines: list[str]) -> str:
    blocks: list[str] = []
    bullets: list[str] = []

    def flush_bullets() -> None:
        nonlocal bullets
        if not bullets:
            return
        items = "".join(f"<li>{render_text(item)}</li>" for item in bullets)
        blocks.append(f"<ul>{items}</ul>")
        bullets = []

    for raw in lines:
        line = raw.strip()
        if not line:
            flush_bullets()
            continue

        if line.startswith("- "):
            bullets.append(line[2:].strip())
            continue

        flush_bullets()

        if line == "OR":
            blocks.append('<p class="or-sep">OR</p>')
            continue

        if line.startswith("### "):
            blocks.append(f"<h5>{render_text(line[4:].strip())}</h5>")
            continue

        if line.startswith("## "):
            blocks.append(f"<h4>{render_text(line[3:].strip())}</h4>")
            continue

        if line.startswith("# "):
            blocks.append(f"<h3>{render_text(line[2:].strip())}</h3>")
            continue

        if line.startswith(">"):
            option_text = line[1:].strip()
            blocks.append(f'<p class="sub-option">{render_text(option_text)}</p>')
            continue

        if "Note to GM!" in line:
            prefix, _, suffix = line.partition("Note to GM!")
            prefix = prefix.strip()
            suffix = suffix.strip()
            if prefix:
                blocks.append(f"<p>{render_text(prefix)}</p>")
            blocks.append(
                '<details class="gm-note"><summary>GM Note</summary><p>'
                + render_text(suffix)
                + "</p></details>"
            )
            continue

        if re.match(r"^[A-Za-z][A-Za-z\s]+$", line) and len(line.split()) <= 4:
            blocks.append(f"<h4>{render_text(line)}</h4>")
            continue

        blocks.append(f"<p>{render_text(line)}</p>")

    flush_bullets()
    return "\n".join(blocks)


def section_sort_key(section: Section) -> int:
    title = section.title.lower()
    order = {
        "stats": 0,
        "skills": 1,
        "checks": 2,
        "rules of play": 3,
        "optional rules section": 4,
    }
    return order.get(title, 99)


def render_html(sections: list[Section]) -> str:
    core_sections = [s for s in sections if s.title.lower() != "optional rules section"]
    core_sections = sorted(core_sections, key=section_sort_key)
    optional = next((s for s in sections if s.title.lower() == "optional rules section"), None)
    modules = split_optional_modules(optional) if optional else []

    nav_links = []
    for section in core_sections:
        sid = slugify(section.title)
        nav_links.append(f'<a href="#{sid}">{html.escape(section.title)}</a>')
    if modules:
        nav_links.append('<a href="#optional-rules">Optional Rules</a>')

    core_html = []
    for section in core_sections:
        sid = slugify(section.title)
        core_html.append(
            f'<section id="{sid}" class="rule-section">\n'
            f"<h2>{html.escape(section.title)}</h2>\n"
            f"{render_blocks(section.lines)}\n"
            "</section>"
        )

    optional_html = ""
    if modules:
        module_html = []
        for module in modules:
            msid = slugify(module.title)
            module_html.append(
                '<details class="module">'
                f'<summary id="{msid}">{html.escape(module.title)}</summary>'
                f"{render_blocks(module.lines)}"
                "</details>"
            )
        optional_html = (
            '<section id="optional-rules" class="rule-section optional">'
            + '<details><summary>Optional Rules</summary>'
            + "".join(module_html)
            + "</details></section>"
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Core Engine Rules</title>
  <style>
    @font-face {{
      font-family: 'Polymath';
      src: url('./assets/fonts/Polymath.otf') format('opentype');
      font-style: normal;
      font-weight: 400;
    }}
    @font-face {{
      font-family: 'Polymath';
      src: url('./assets/fonts/Polymath-Bold.otf') format('opentype');
      font-style: normal;
      font-weight: 700;
    }}
    :root {{
      --bg: #f4efe6;
      --bg2: #ede2cc;
      --ink: #1d1a16;
      --card: #fffbf3;
      --line: #d4c6ab;
      --note: #fff0cc;
      --accent: #74411b;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      background: radial-gradient(circle at 20% 10%, var(--bg2), var(--bg));
      font-family: 'Trebuchet MS', 'Gill Sans', 'Segoe UI', sans-serif;
      line-height: 1.45;
    }}
    .wrap {{ max-width: 940px; margin: 0 auto; padding: 1rem; }}
    header {{ background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 1rem; }}
    h1 {{ font-size: clamp(1.4rem, 2.4vw, 2.1rem); margin: 0 0 .4rem; }}
    nav {{ position: sticky; top: 0; z-index: 10; margin: .9rem 0; padding: .5rem .7rem; background: #fff8ebd9; backdrop-filter: blur(3px); border: 1px solid var(--line); border-radius: 10px; display: flex; flex-wrap: wrap; gap: .6rem; }}
    nav a {{ color: #1f4f6f; text-decoration: none; font-weight: 700; }}
    .rule-section {{ background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 1rem; margin-bottom: .9rem; }}
    h2 {{ margin: 0 0 .6rem; font-size: 1.2rem; }}
    h3 {{ margin: .9rem 0 .35rem; font-size: 1.06rem; color: #3b2a17; }}
    h4 {{ margin: .8rem 0 .35rem; font-size: 1rem; color: var(--accent); }}
    h5 {{ margin: .65rem 0 .3rem; font-size: .95rem; color: #5a3518; letter-spacing: .02em; }}
    p {{ margin: .35rem 0; }}
    ul {{ margin: .25rem 0 .5rem 1.2rem; padding: 0; }}
    .sub-option {{ margin: .2rem 0 .55rem 2rem; padding: .15rem .5rem; border-left: 3px solid #c5a170; color: #4f3b27; font-style: italic; }}
    .gm-note {{ background: var(--note); border-left: 4px solid #a56833; padding: .45rem .55rem; border-radius: 6px; margin: .45rem 0; }}
    .gm-note > summary {{ margin: 0; }}
    .gm-note > p {{ margin: .45rem 0 0; }}
    .or-sep {{ text-align: center; font-weight: 700; letter-spacing: .08em; color: var(--accent); }}
    details > summary {{ cursor: pointer; font-weight: 800; margin-bottom: .5rem; }}
    .module {{ border: 1px solid var(--line); border-radius: 8px; padding: .5rem .65rem; margin: .55rem 0; background: #fffdf8; }}
    .dice-token {{ display: inline-flex; align-items: baseline; gap: .3rem; }}
    .dice-glyph {{ font-family: 'Polymath', serif; font-weight: 700; font-size: 1.06em; letter-spacing: .01em; }}
    .dice-text {{ font-size: .87em; opacity: .72; }}
    footer {{ padding: .5rem .2rem 1.1rem; font-size: .9rem; color: #614f3a; }}
    @media (max-width: 700px) {{
      .wrap {{ padding: .7rem; }}
      nav {{ position: static; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1>Core Engine Rules</h1>
    </header>
    <nav>{''.join(nav_links)}</nav>
    {''.join(core_html)}
    {optional_html}
    <footer>Dice symbols use Polymath by Jesse Ross (SIL OFL 1.1).</footer>
  </div>
</body>
</html>
"""


def copy_fonts(output_path: Path) -> None:
    root = Path(__file__).resolve().parent.parent
    source_dir = root / "Polymath"
    files = ["Polymath.otf", "Polymath-Bold.otf"]

    for filename in files:
        src = source_dir / filename
        if not src.exists():
            raise FileNotFoundError(f"Missing font file: {src}")

    target_dir = output_path.parent / "assets" / "fonts"
    target_dir.mkdir(parents=True, exist_ok=True)

    for filename in files:
        shutil.copy2(source_dir / filename, target_dir / filename)


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        print(f"Input file does not exist: {input_path}", file=sys.stderr)
        return 1

    text = input_path.read_text(encoding="utf-8")
    sections = parse_sections(text)
    if not sections:
        print("Failed to parse sections from input file.", file=sys.stderr)
        return 1

    html_text = render_html(sections)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_text, encoding="utf-8")
    copy_fonts(output_path)
    print(f"Generated {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
