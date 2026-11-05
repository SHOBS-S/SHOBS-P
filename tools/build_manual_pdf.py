"""Build the SHOBS-P User's Manual PDF from docs/manual/*.md.

pandoc (Markdown -> HTML) for each chapter, one HTML book with a title page and
contents, then headless Chromium (Playwright) prints it to a Letter PDF with
page numbers. Same method as the SHOBS-SCHED manual.

    python tools/build_manual_pdf.py [output.pdf]
"""
import datetime
import html
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAN = ROOT / "docs" / "manual"
VERSION = "2.3.1"
EDITION = "Edition for version 2.3.1"
TITLE = "SHOBS-P User's Manual"

CSS = """
@page { size: Letter; margin: 0.75in 0.8in 0.8in 0.8in; }
html { font-family: "DejaVu Sans", "Segoe UI", Arial, sans-serif; font-size: 10pt;
       line-height: 1.45; color: #1d1d1f; }
body { margin: 0; }
h1, h2, h3, h4 { color: #1f3a5f; line-height: 1.25; break-after: avoid; }
h1 { font-size: 20pt; border-bottom: 2px solid #1f3a5f; padding-bottom: 4px; margin: 0 0 14px; }
h2 { font-size: 14pt; margin: 22px 0 8px; }
h3 { font-size: 11.5pt; margin: 18px 0 6px; }
p, li { orphans: 3; widows: 3; }
a { color: #1f5fa8; text-decoration: none; }
code { font-family: "DejaVu Sans Mono", Consolas, monospace; font-size: 8.8pt;
       background: #f2f3f5; padding: 0 2px; border-radius: 2px; }
pre { background: #f2f3f5; padding: 8px 10px; font-size: 8.5pt; white-space: pre-wrap;
      break-inside: avoid; }
pre code { background: none; padding: 0; }
blockquote { margin: 10px 0; padding: 6px 12px; border-left: 3px solid #9fb3c8;
             background: #f6f8fa; color: #333; }
table { border-collapse: collapse; width: 100%; margin: 10px 0; font-size: 9pt; break-inside: auto; }
th, td { border: 1px solid #c8ced6; padding: 4px 6px; vertical-align: top; text-align: left; }
th { background: #e8eef5; }
tr { break-inside: avoid; }
figure { margin: 12px 0; text-align: center; break-inside: avoid; }
figure img, p > img { max-width: 100%; max-height: 7.6in; border: 1px solid #c8ced6; }
figcaption { font-size: 8.5pt; color: #555; margin-top: 4px; }
.chapter { break-before: page; }
.nav { font-size: 8.5pt; color: #666; }
hr { border: none; border-top: 1px solid #d0d5db; margin: 18px 0 8px; }
.title { height: 9in; display: flex; flex-direction: column; justify-content: center;
         align-items: center; text-align: center; }
.title h1 { border: none; font-size: 28pt; margin-bottom: 10px; }
.title .sub { font-size: 13pt; color: #333; }
.title .tag { font-size: 11pt; color: #555; font-style: italic; margin-top: 6px; }
.title .date { font-size: 10pt; color: #555; margin-top: 26px; }
.contents { break-before: page; }
.contents h1 { border: none; }
.contents .part { font-weight: bold; color: #1f3a5f; margin: 14px 0 4px; }
.contents a { display: block; margin: 3px 0 3px 12px; }
"""


def chapter_files():
    return sorted(p for p in MAN.glob("[0-9][0-9]-*.md"))


def slug(path):
    return "ch-" + path.stem


def to_html(md_path):
    out = subprocess.run(
        ["pandoc", str(md_path), "-f", "gfm+implicit_figures", "-t", "html5",
         "--wrap=none", "--resource-path", str(MAN)],
        check=True, capture_output=True, text=True).stdout
    return out


def fix_links(body, names):
    # links between chapters -> anchors in the one book
    def repl(m):
        target = m.group(1)
        base = target.split("#")[0]
        if base in names:
            return 'href="#%s"' % slug(pathlib.Path(base))
        if base == "_shots.md" or base == "README.md":
            return 'href="#contents"'
        return m.group(0)
    body = re.sub(r'href="([^"]+\.md(?:#[^"]*)?)"', repl, body)
    # images: absolute file paths so Chromium finds them
    body = re.sub(r'src="(img/[^"]+)"',
                  lambda m: 'src="%s"' % (MAN / m.group(1)).as_uri(), body)
    # a picture on its own line -> figure with its alt text as the caption
    def fig(m):
        tag = m.group(1)
        alt = re.search(r'alt="([^"]*)"', tag)
        cap = '<figcaption>%s</figcaption>' % alt.group(1) if alt and alt.group(1) else ""
        return '<figure>%s%s</figure>' % (tag, cap)
    body = re.sub(r'<p>\s*(<img [^>]*>)\s*</p>', fig, body)
    # the Previous / Next line
    body = re.sub(r"<p>(Previous:.*?)</p>", r'<p class="nav">\1</p>', body, flags=re.S)
    return body


def contents(chapters):
    readme = (MAN / "README.md").read_text(encoding="utf-8")
    parts = []
    for line in readme.splitlines():
        m = re.match(r"## (Part .*)", line)
        if m:
            parts.append((m.group(1), []))
        m = re.match(r"\d+\.\s+\*\*\[(.+?)\]\((\d\d-[^)]+\.md)\)", line)
        if m and parts:
            parts[-1][1].append((m.group(1), m.group(2)))
    out = ['<section class="contents" id="contents"><h1>Contents</h1>']
    for title, items in parts:
        out.append('<div class="part">%s</div>' % html.escape(title))
        for name, f in items:
            num = int(f[:2])
            out.append('<a href="#%s">%d. %s</a>' % (slug(pathlib.Path(f)), num, html.escape(name)))
    out.append("</section>")
    return "\n".join(out)


def build(pdf_out):
    chapters = chapter_files()
    names = {p.name for p in chapters}
    today = datetime.date.today()
    parts = [
        '<section class="title"><h1>%s</h1>' % TITLE,
        '<div class="sub">Shiloh Hill Observatory Photometry, version %s</div>' % VERSION,
        '<div class="tag">Variable stars, exoplanet transits and field scans, from your frames to AAVSO and ExoFOP.</div>',
        '<div class="date">%s, %d %s %d</div></section>' % (EDITION,
            today.day, today.strftime("%B"), today.year),
        contents(chapters),
    ]
    for p in chapters:
        parts.append('<section class="chapter" id="%s">%s</section>'
                     % (slug(p), fix_links(to_html(p), names)))
    doc = ('<!doctype html><html><head><meta charset="utf-8"><title>%s</title>'
           '<style>%s</style></head><body>%s</body></html>') % (TITLE, CSS, "\n".join(parts))
    html_path = pathlib.Path(pdf_out).with_suffix(".html")
    html_path.write_text(doc, encoding="utf-8")

    from playwright.sync_api import sync_playwright
    footer = ('<div style="width:100%;font-size:7pt;color:#777;text-align:center;'
              'font-family:DejaVu Sans,sans-serif;">' + TITLE + ' ' + VERSION +
              ' · <span class="pageNumber"></span> / <span class="totalPages"></span></div>')
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_page()
        page.goto(html_path.as_uri(), wait_until="networkidle")
        page.pdf(path=str(pdf_out), format="Letter", print_background=True,
                 display_header_footer=True, header_template="<div></div>",
                 footer_template=footer, prefer_css_page_size=True)
        b.close()
    print("wrote", pdf_out)


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "SHOBS-P Users Manual %s.pdf" % VERSION))
