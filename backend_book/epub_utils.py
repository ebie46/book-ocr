"""EPUB generation.

Tries pypandoc/pandoc first when available (richer formatting). Falls back
to a minimal, dependency-free EPUB 3 builder using only stdlib (zipfile)
so the export button works even without pandoc installed.
"""
from __future__ import annotations

import io
import re
import uuid
import zipfile
from datetime import datetime, timezone
from typing import Optional


def _have_pandoc() -> bool:
    try:
        import pypandoc  # type: ignore

        pypandoc.get_pandoc_version()
        return True
    except Exception:
        return False


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _split_chapters(text: str) -> list[tuple[str, str]]:
    """Split combined OCR text on the '===== Halaman N =====' markers
    we use when exporting a multi-page document.

    Returns a list of (chapter_title, body_text). If no markers are
    found, the whole text becomes a single untitled chapter.
    """
    pattern = re.compile(r"^=+\s*(Halaman\s+\d+)\s*=+\s*$", re.MULTILINE)
    matches = list(pattern.finditer(text))
    if not matches:
        return [("Isi", text.strip())]
    chapters: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        title = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip("\n").strip()
        chapters.append((title, body))
    return chapters


def _paragraphs_to_html(body: str) -> str:
    """Convert plain text into safe XHTML paragraphs."""
    if not body.strip():
        return ""
    blocks = re.split(r"\n\s*\n", body.strip())
    out: list[str] = []
    for block in blocks:
        # keep single-line breaks within a block as <br/>
        lines = [_xml_escape(line) for line in block.splitlines()]
        joined = "<br/>\n".join(lines)
        out.append(f"<p>{joined}</p>")
    return "\n".join(out)


def text_to_epub(
    text: str,
    *,
    title: str,
    author: str = "Book OCR",
    language: str = "id",
) -> bytes:
    """Return EPUB bytes for the given OCR text. Uses pandoc if available."""
    if _have_pandoc():
        try:
            return _epub_with_pandoc(text, title=title, author=author, language=language)
        except Exception:
            # fall back below
            pass
    return _epub_minimal(text, title=title, author=author, language=language)


def _epub_with_pandoc(text: str, *, title: str, author: str, language: str) -> bytes:
    import os
    import tempfile

    import pypandoc  # type: ignore

    md_lines: list[str] = [f"---", f'title: "{title}"', f'author: "{author}"',
                           f'lang: "{language}"', "---", ""]
    chapters = _split_chapters(text)
    if len(chapters) == 1 and chapters[0][0] == "Isi":
        md_lines.append(chapters[0][1])
    else:
        for ch_title, body in chapters:
            md_lines.append(f"# {ch_title}")
            md_lines.append("")
            md_lines.append(body)
            md_lines.append("")
    md = "\n".join(md_lines)

    with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as tmp:
        out_path = tmp.name
    try:
        pypandoc.convert_text(
            md, to="epub3", format="md", outputfile=out_path,
            extra_args=[f"--metadata=title:{title}", f"--metadata=author:{author}",
                        f"--metadata=lang:{language}"],
        )
        with open(out_path, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass


# -------- minimal EPUB 3 builder ---------------------------------------------

_CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

_CSS = """body { font-family: Georgia, serif; line-height: 1.55; margin: 1em; }
h1 { font-size: 1.4em; margin-top: 1.6em; }
p { margin: 0 0 1em 0; text-align: justify; }
"""


def _chapter_xhtml(title: str, body_html: str, language: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{language}">
<head>
  <meta charset="utf-8"/>
  <title>{_xml_escape(title)}</title>
  <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>
  <h1>{_xml_escape(title)}</h1>
  {body_html}
</body>
</html>
"""


def _opf(title: str, author: str, language: str, chapters: list[tuple[str, str]], book_id: str) -> str:
    items: list[str] = []
    spine: list[str] = []
    for i, _ in enumerate(chapters, 1):
        idref = f"ch{i}"
        items.append(
            f'<item id="{idref}" href="ch{i}.xhtml" media-type="application/xhtml+xml"/>'
        )
        spine.append(f'<itemref idref="{idref}"/>')
    nav_item = '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
    css_item = '<item id="css" href="style.css" media-type="text/css"/>'
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="{language}">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">urn:uuid:{book_id}</dc:identifier>
    <dc:title>{_xml_escape(title)}</dc:title>
    <dc:creator>{_xml_escape(author)}</dc:creator>
    <dc:language>{language}</dc:language>
    <meta property="dcterms:modified">{now}</meta>
  </metadata>
  <manifest>
    {nav_item}
    {css_item}
    {chr(10).join(items)}
  </manifest>
  <spine>
    <itemref idref="nav" linear="no"/>
    {chr(10).join(spine)}
  </spine>
</package>
"""


def _nav(chapters: list[tuple[str, str]], language: str) -> str:
    lis = "\n".join(
        f'      <li><a href="ch{i}.xhtml">{_xml_escape(t)}</a></li>'
        for i, (t, _) in enumerate(chapters, 1)
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{language}">
<head><meta charset="utf-8"/><title>Daftar Isi</title></head>
<body>
  <nav epub:type="toc" id="toc">
    <h1>Daftar Isi</h1>
    <ol>
{lis}
    </ol>
  </nav>
</body>
</html>
"""


def _epub_minimal(text: str, *, title: str, author: str, language: str) -> bytes:
    chapters = _split_chapters(text)
    chapters_html = [(t, _paragraphs_to_html(b)) for t, b in chapters]
    book_id = str(uuid.uuid4())

    buf = io.BytesIO()
    # ZIP_STORED for the mimetype (must be uncompressed and first)
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zi = zipfile.ZipInfo("mimetype")
        zi.compress_type = zipfile.ZIP_STORED
        zf.writestr(zi, "application/epub+zip")

        zf.writestr("META-INF/container.xml", _CONTAINER_XML)
        zf.writestr("OEBPS/style.css", _CSS)
        zf.writestr("OEBPS/nav.xhtml", _nav(chapters_html, language))
        for i, (ch_title, body_html) in enumerate(chapters_html, 1):
            zf.writestr(
                f"OEBPS/ch{i}.xhtml",
                _chapter_xhtml(ch_title, body_html, language),
            )
        zf.writestr("OEBPS/content.opf", _opf(title, author, language, chapters_html, book_id))

    return buf.getvalue()
