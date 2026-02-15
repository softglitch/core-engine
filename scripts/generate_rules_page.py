#!/usr/bin/env python3
"""Generate a concise static rules page from RULES.md."""

from __future__ import annotations

import argparse
import base64
import html
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

SECTION_RE = re.compile(r"^==\s*(.+?)\s*==$")
OPTIONAL_MODULE_RE = re.compile(r"^—\s*(.+?)\s*—$")
DICE_TOKEN_RE = re.compile(r"\b(\d*)d(4|6|8|10|12|20)\b")
DR_TOKEN_RE = re.compile(r"\bDR(\d+)\b")


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
        count_text = match.group(1)
        die = f"d{match.group(2)}"
        count = int(count_text) if count_text else 1
        repeat_count = max(1, min(count, 12))
        safe_token = html.escape(token)
        safe_die = html.escape(die)
        glyphs = "".join(
            f'<span class="dice-glyph">{safe_die}</span>' for _ in range(repeat_count)
        )
        return (
            '<span class="dice-token" aria-label="'
            + safe_token
            + '">'
            + glyphs
            + "</span>"
        )

    return DICE_TOKEN_RE.sub(repl, escaped_text)


def render_text(text: str) -> str:
    normalized = compress_line(text).replace("->", "→")
    escaped = html.escape(normalized)
    rendered = apply_dice_markup(escaped)
    rendered = rendered.replace(
        "→",
        '<span class="dice-arrow" aria-hidden="true">→</span>',
    )
    return DR_TOKEN_RE.sub(r'<span class="dr-tag">DR\1</span>', rendered)


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

    idx = 0
    while idx < len(lines):
        raw = lines[idx]
        line = raw.strip()
        if not line:
            flush_bullets()
            idx += 1
            continue

        if line.startswith("- "):
            bullets.append(line[2:].strip())
            idx += 1
            continue

        flush_bullets()

        if line == "---":
            next_delim = None
            probe = idx + 1
            while probe < len(lines):
                if lines[probe].strip() == "---":
                    next_delim = probe
                    break
                probe += 1

            if next_delim is None:
                idx += 1
                continue

            block_lines: list[str] = []
            idx += 1
            while idx < next_delim:
                inner = lines[idx].strip()
                if inner:
                    block_lines.append(inner)
                idx += 1

            is_ref_block = False
            if len(block_lines) >= 2:
                first = block_lines[0].strip()
                second = block_lines[1].strip()
                has_kv_rows = any(":" in row for row in block_lines[2:])
                looks_like_item = bool(re.match(r"^\d+\.\s", first))
                has_forbidden_markers = first.startswith("Note to GM!") or second.startswith("#")
                is_ref_block = (looks_like_item or has_kv_rows) and not has_forbidden_markers

            if is_ref_block:
                header = render_text(block_lines[0])
                desc = render_text(block_lines[1])
                rows: list[str] = []
                for raw_row in block_lines[2:]:
                    label, sep, content = raw_row.partition(":")
                    if sep:
                        rows.append(
                            "<tr><th>"
                            + render_text(label.strip())
                            + "</th><td>"
                            + render_text(content.strip())
                            + "</td></tr>"
                        )
                    else:
                        rows.append("<tr><td colspan=\"2\">" + render_text(raw_row) + "</td></tr>")
                table_html = (
                    "<table><tbody>" + "".join(rows) + "</tbody></table>"
                    if rows
                    else ""
                )
                blocks.append(
                    '<section class="ref-block"><h5 class="ref-head">'
                    + header
                    + '</h5><p class="ref-desc">'
                    + desc
                    + "</p>"
                    + table_html
                    + "</section>"
                )
            elif block_lines:
                blocks.append(render_blocks(block_lines))

            continue

        if line == "OR":
            blocks.append('<p class="or-sep">OR</p>')
            idx += 1
            continue

        if line.startswith("### "):
            blocks.append(f"<h5>{render_text(line[4:].strip())}</h5>")
            idx += 1
            continue

        if line.startswith("## "):
            blocks.append(f"<h4>{render_text(line[3:].strip())}</h4>")
            idx += 1
            continue

        if line.startswith("# "):
            blocks.append(f"<h3>{render_text(line[2:].strip())}</h3>")
            idx += 1
            continue

        if line.startswith(">"):
            option_text = line[1:].strip()
            blocks.append(f'<p class="sub-option">{render_text(option_text)}</p>')
            idx += 1
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
            idx += 1
            continue

        if re.match(r"^[A-Za-z][A-Za-z\s]+$", line) and len(line.split()) <= 4:
            blocks.append(f"<h4>{render_text(line)}</h4>")
            idx += 1
            continue

        blocks.append(f"<p>{render_text(line)}</p>")
        idx += 1

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


def load_font_sources() -> tuple[str, str]:
    root = Path(__file__).resolve().parent.parent
    source_dir = root / "Polymath"
    regular_path = source_dir / "Polymath.otf"
    bold_path = source_dir / "Polymath-Bold.otf"

    if not regular_path.exists():
        raise FileNotFoundError(f"Missing font file: {regular_path}")
    if not bold_path.exists():
        raise FileNotFoundError(f"Missing font file: {bold_path}")

    regular_b64 = base64.b64encode(regular_path.read_bytes()).decode("ascii")
    bold_b64 = base64.b64encode(bold_path.read_bytes()).decode("ascii")

    regular_src = (
        f"url('data:font/otf;base64,{regular_b64}') format('opentype'), "
        "url('./assets/fonts/Polymath.otf') format('opentype')"
    )
    bold_src = (
        f"url('data:font/otf;base64,{bold_b64}') format('opentype'), "
        "url('./assets/fonts/Polymath-Bold.otf') format('opentype')"
    )
    return regular_src, bold_src


def render_html(sections: list[Section], regular_font_src: str, bold_font_src: str) -> str:
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
      src: {regular_font_src};
      font-style: normal;
      font-weight: 400;
      font-display: swap;
    }}
    @font-face {{
      font-family: 'Polymath';
      src: {bold_font_src};
      font-style: normal;
      font-weight: 700;
      font-display: swap;
    }}
    :root {{
      --bg: #f4efe6;
      --bg2: #ede2cc;
      --ink: #1d1a16;
      --card: #fffbf3;
      --line: #d4c6ab;
      --note: #fff0cc;
      --accent: #74411b;
      --navbg: #fff8ebd9;
      --link: #1f4f6f;
      --subopt: #4f3b27;
      --suboptline: #c5a170;
      --drbg: #f6ebd7;
      --drline: #e3cfad;
      --refdesc: #5c4a36;
      --footer: #614f3a;
      --h3: #3b2a17;
      --h5: #5a3518;
      --noteborder: #a56833;
      --refcard: #fffdf8;
      --refhead: #4d2f17;
      --refline: #eadfcb;
      --refth: #5d3a1f;
      --summary: #2a2118;
    }}
    [data-theme="dark"] {{
      --bg: #171a1f;
      --bg2: #202833;
      --ink: #ece7db;
      --card: #232b35;
      --line: #3a4757;
      --note: #313321;
      --accent: #e0b16c;
      --navbg: #1e2630dd;
      --link: #88c2e8;
      --subopt: #d5c4ac;
      --suboptline: #7f6948;
      --drbg: #3e3324;
      --drline: #705838;
      --refdesc: #d2c4b0;
      --footer: #af9f89;
      --h3: #e7d9c5;
      --h5: #e0c091;
      --noteborder: #b88d52;
      --refcard: #1f2731;
      --refhead: #e2b97f;
      --refline: #384555;
      --refth: #e2c79d;
      --summary: #e7ddce;
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
    nav {{ position: sticky; top: 0; z-index: 10; margin: .9rem 0; padding: .5rem .7rem; background: var(--navbg); backdrop-filter: blur(3px); border: 1px solid var(--line); border-radius: 10px; display: flex; flex-wrap: wrap; gap: .6rem; }}
    nav a {{ color: var(--link); text-decoration: none; font-weight: 700; }}
    .rule-section {{ background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 1rem; margin-bottom: .9rem; }}
    h2 {{ margin: 0 0 .6rem; font-size: 1.2rem; }}
    h3 {{ margin: .9rem 0 .35rem; font-size: 1.06rem; color: var(--h3); }}
    h4 {{ margin: .8rem 0 .35rem; font-size: 1rem; color: var(--accent); }}
    h5 {{ margin: .65rem 0 .3rem; font-size: .95rem; color: var(--h5); letter-spacing: .02em; }}
    p {{ margin: .35rem 0; }}
    ul {{ margin: .25rem 0 .5rem 1.2rem; padding: 0; }}
    .sub-option {{ margin: .2rem 0 .55rem 2rem; padding: .15rem .5rem; border-left: 3px solid var(--suboptline); color: var(--subopt); font-style: italic; }}
    .gm-note {{ background: var(--note); border-left: 4px solid var(--noteborder); padding: .45rem .55rem; border-radius: 6px; margin: .45rem 0; }}
    .gm-note > summary {{ margin: 0; font-size: .9em; }}
    .gm-note > p {{ margin: .45rem 0 0; font-size: .92em; }}
    .or-sep {{ text-align: center; font-weight: 700; letter-spacing: .08em; color: var(--accent); }}
    details > summary {{ cursor: pointer; font-weight: 800; margin-bottom: .5rem; color: var(--summary); }}
    .module {{ border: 1px solid var(--line); border-radius: 8px; padding: .5rem .65rem; margin: .55rem 0; background: var(--card); }}
    .dice-token {{ display: inline-flex; align-items: baseline; gap: .3rem; }}
    .dice-glyph {{ font-family: 'Polymath', serif; font-weight: 700; font-size: 1.4em; letter-spacing: .01em; font-variant-ligatures: common-ligatures contextual; font-feature-settings: 'liga' 1, 'calt' 1; text-transform: lowercase; }}
    .dice-arrow {{ display: inline-block; font-size: 1.2em; transform: translateY(-0.12em); }}
    .dr-tag {{ color: var(--accent); background: var(--drbg); border: 1px solid var(--drline); border-radius: 4px; padding: 0 .22em; font-weight: 700; }}
    .ref-block {{ border: 1px solid var(--line); border-radius: 10px; padding: .6rem .7rem; margin: .55rem 0; background: var(--refcard); }}
    .ref-head {{ margin: 0 0 .15rem; color: var(--refhead); }}
    .ref-desc {{ margin: 0 0 .5rem; color: var(--refdesc); font-style: italic; }}
    .ref-block table {{ width: 100%; border-collapse: collapse; }}
    .ref-block th, .ref-block td {{ text-align: left; padding: .3rem .35rem; border-top: 1px solid var(--refline); vertical-align: top; }}
    .ref-block th {{ width: 28%; color: var(--refth); font-weight: 700; }}
    footer {{ padding: .5rem .2rem 1.1rem; font-size: .9rem; color: var(--footer); }}
    .theme-row {{ position: fixed; top: .75rem; right: .75rem; z-index: 30; margin: 0; }}
    .theme-toggle {{ border: 1px solid var(--line); background: var(--card); color: var(--ink); padding: .35rem .62rem; border-radius: 999px; font: inherit; cursor: pointer; box-shadow: 0 2px 10px rgba(0,0,0,.08); }}
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
      <div class="theme-row"><button id="theme-toggle" class="theme-toggle" type="button" aria-label="Toggle theme">☾</button></div>
    </header>
    <nav>{''.join(nav_links)}</nav>
    {''.join(core_html)}
    {optional_html}
    <footer>Dice symbols use Polymath by Jesse Ross (SIL OFL 1.1).</footer>
  </div>
  <script>
    (function() {{
      const storageKey = 'core-engine-theme';
      const root = document.documentElement;
      const btn = document.getElementById('theme-toggle');
      if (!btn) return;

      const systemPrefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
      const saved = localStorage.getItem(storageKey);
      const initial = saved || (systemPrefersDark ? 'dark' : 'light');

      function setTheme(theme) {{
        if (theme === 'dark') {{
          root.setAttribute('data-theme', 'dark');
          btn.textContent = '☀';
          btn.setAttribute('aria-label', 'Switch to light theme');
        }} else {{
          root.removeAttribute('data-theme');
          btn.textContent = '☾';
          btn.setAttribute('aria-label', 'Switch to dark theme');
        }}
      }}

      setTheme(initial);
      btn.addEventListener('click', function() {{
        const current = root.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
        const next = current === 'dark' ? 'light' : 'dark';
        setTheme(next);
        localStorage.setItem(storageKey, next);
      }});
    }})();
  </script>
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

    regular_font_src, bold_font_src = load_font_sources()
    html_text = render_html(sections, regular_font_src, bold_font_src)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_text, encoding="utf-8")
    copy_fonts(output_path)
    print(f"Generated {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
