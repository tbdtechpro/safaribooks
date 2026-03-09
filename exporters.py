"""
exporters.py — MarkdownExporter and RagExporter for SafariBooks.

MarkdownExporter: converts OEBPS XHTML chapters to GFM Markdown.
RagExporter: chunks Markdown by heading boundaries into JSONL records.
"""

import json
import os
import re
import shutil

from lxml import etree


# ---------------------------------------------------------------------------
# XHTML → GFM Markdown converter
# ---------------------------------------------------------------------------

_XHTML_NS = "http://www.w3.org/1999/xhtml"


def _tag(el) -> str:
    """Return local tag name, stripping any namespace."""
    tag = el.tag
    if isinstance(tag, str) and tag.startswith("{"):
        tag = tag.split("}", 1)[1]
    return tag.lower()


def _attr(el, name: str, default: str = "") -> str:
    """Get attribute value, checking both namespaced and plain forms."""
    val = el.get(name, el.get("{%s}%s" % (_XHTML_NS, name), default))
    return val or default


def _data_type(el) -> str:
    return el.get("data-type", "")


def xhtml_to_markdown(el, _depth: int = 0) -> str:
    """Recursively convert an lxml Element tree to GFM Markdown string."""
    tag = _tag(el)

    # Strip entirely
    if tag in ("script", "style", "head"):
        return ""
    if _data_type(el) == "indexterm":
        return ""

    # Gather child content (recursive)
    def children_md(node, depth=_depth) -> str:
        parts = []
        if node.text:
            parts.append(node.text)
        for child in node:
            parts.append(xhtml_to_markdown(child, depth))
            if child.tail:
                parts.append(child.tail)
        return "".join(parts)

    # Headings h1-h6
    if re.match(r'^h[1-6]$', tag):
        level = int(tag[1])
        inner = children_md(el).strip()
        return "\n\n" + "#" * level + " " + inner + "\n\n"

    # Paragraphs
    if tag == "p":
        inner = children_md(el).strip()
        if not inner:
            return ""
        return "\n\n" + inner + "\n\n"

    # Inline code
    if tag == "code":
        parent_tag = _tag(el.getparent()) if el.getparent() is not None else ""
        if parent_tag == "pre":
            # Will be handled by pre
            inner = "".join(el.itertext())
            return inner
        inner = "".join(el.itertext()).replace("`", "\\`")
        return "`" + inner + "`"

    # Code blocks
    if tag == "pre":
        # Extract raw text (skip markdown conversion inside pre)
        raw = "".join(el.itertext())
        # Try to find a language hint from class attribute
        lang = ""
        cls = _attr(el, "class")
        if cls:
            for part in cls.split():
                if part.startswith("language-") or part.startswith("lang-"):
                    lang = part.split("-", 1)[1]
                    break
        return "\n\n```" + lang + "\n" + raw.rstrip("\n") + "\n```\n\n"

    # Emphasis / strong
    if tag == "em" or tag == "i":
        inner = children_md(el)
        return "*" + inner + "*"
    if tag == "strong" or tag == "b":
        inner = children_md(el)
        return "**" + inner + "**"

    # Links
    if tag == "a":
        href = _attr(el, "href")
        inner = children_md(el).strip()
        if not inner:
            inner = href
        if href:
            return "[" + inner + "](" + href + ")"
        return inner

    # Images
    if tag == "img":
        src = _attr(el, "src")
        alt = _attr(el, "alt", "image")
        # Rewrite path to images/ subfolder
        filename = os.path.basename(src) if src else ""
        return "![" + alt + "](images/" + filename + ")"

    # Lists
    if tag == "ul":
        items = []
        for child in el:
            if _tag(child) == "li":
                content = children_md(child).strip().replace("\n\n", "\n").replace("\n", "\n  ")
                items.append("- " + content)
        return "\n\n" + "\n".join(items) + "\n\n"

    if tag == "ol":
        items = []
        for i, child in enumerate(el, 1):
            if _tag(child) == "li":
                content = children_md(child).strip().replace("\n\n", "\n").replace("\n", "\n   ")
                items.append("%d. %s" % (i, content))
        return "\n\n" + "\n".join(items) + "\n\n"

    # Tables — GFM pipe tables
    if tag == "table":
        return _table_to_md(el)

    # Blockquote / note
    if tag in ("blockquote",) or _data_type(el) in ("note", "tip", "warning", "caution"):
        inner = children_md(el).strip()
        lines = inner.splitlines()
        quoted = "\n".join("> " + line for line in lines)
        return "\n\n" + quoted + "\n\n"

    # Figure: image + caption
    if tag == "figure":
        img_el = el.find(".//{%s}img" % _XHTML_NS)
        if img_el is None:
            img_el = el.find(".//img")
        caption_el = el.find(".//{%s}figcaption" % _XHTML_NS)
        if caption_el is None:
            caption_el = el.find(".//figcaption")
        parts = []
        if img_el is not None:
            parts.append(xhtml_to_markdown(img_el, _depth))
        if caption_el is not None:
            cap_text = "".join(caption_el.itertext()).strip()
            if cap_text:
                parts.append("\n*" + cap_text + "*")
        return "\n\n" + "".join(parts) + "\n\n"

    # Horizontal rule
    if tag == "hr":
        return "\n\n---\n\n"

    # Line break
    if tag == "br":
        return "  \n"

    # Span / div / section / article / body / html — pass-through
    if tag in ("span", "div", "section", "article", "body", "html", "aside",
               "header", "footer", "main", "nav", "li", "dt", "dd", "dl",
               "figcaption", "caption"):
        return children_md(el)

    # Default: render children
    return children_md(el)


def _table_to_md(table_el) -> str:
    """Convert an HTML table element to a GFM pipe table."""
    rows = []
    for row_el in table_el.iter():
        if _tag(row_el) == "tr":
            cells = []
            for cell_el in row_el:
                ct = _tag(cell_el)
                if ct in ("td", "th"):
                    text = " ".join("".join(cell_el.itertext()).split())
                    text = text.replace("|", "\\|")
                    cells.append(text)
            if cells:
                rows.append(cells)

    if not rows:
        return ""

    col_count = max(len(r) for r in rows)
    # Pad rows to equal width
    rows = [r + [""] * (col_count - len(r)) for r in rows]

    header = rows[0]
    sep = ["---"] * col_count
    body = rows[1:] if len(rows) > 1 else []

    def fmt_row(cells):
        return "| " + " | ".join(cells) + " |"

    lines = [fmt_row(header), fmt_row(sep)] + [fmt_row(r) for r in body]
    return "\n\n" + "\n".join(lines) + "\n\n"


# ---------------------------------------------------------------------------
# MarkdownExporter
# ---------------------------------------------------------------------------

class MarkdownExporter:
    """Convert an OEBPS book directory to GFM Markdown files."""

    def __init__(self, book_id: str, book_path: str, book_info: dict, chapters: list,
                 output_dir: str = "", folder_name: str = ""):
        """
        output_dir:  if set, markdown files go to {output_dir}/{folder_name}/
                     instead of the default {book_path}/markdown/.
        folder_name: the subdirectory name inside output_dir (defaults to book_id
                     when output_dir is set but folder_name is not supplied).
        """
        self.book_id = book_id
        self.book_path = book_path
        self.book_info = book_info
        self.chapters = chapters
        self.oebps = os.path.join(book_path, "OEBPS")
        if output_dir:
            self.md_dir = os.path.join(output_dir, folder_name or book_id)
        else:
            self.md_dir = os.path.join(book_path, "markdown")

    def export(self) -> dict:
        """Export all chapters to markdown/ subfolder.

        Returns a dict {filename: markdown_str} for re-use by RagExporter/DB.
        """
        os.makedirs(self.md_dir, exist_ok=True)
        self._copy_images()

        markdown_map = {}
        for ch in self.chapters:
            filename = ch.get("filename", "")
            if not filename:
                continue
            xhtml_path = os.path.join(self.oebps, filename)
            if not os.path.isfile(xhtml_path):
                continue
            try:
                md = self._convert_xhtml(xhtml_path)
            except RecursionError:
                # Some XHTML files have pathologically deep nesting; skip gracefully.
                md = f"*(Chapter conversion failed: XHTML nesting too deep for {filename})*"
            except Exception as exc:
                md = f"*(Chapter conversion failed: {exc})*"
            markdown_map[filename] = md
            self._write_chapter_md(filename, md)

        self._write_combined_md(markdown_map)
        return markdown_map

    def _convert_xhtml(self, xhtml_path: str) -> str:
        """Parse XHTML and convert to GFM Markdown."""
        try:
            tree = etree.parse(xhtml_path, etree.XMLParser(recover=True))
            root = tree.getroot()
        except etree.XMLSyntaxError:
            with open(xhtml_path, encoding="utf-8", errors="replace") as f:
                raw = f.read()
            root = etree.fromstring(raw.encode("utf-8"), etree.XMLParser(recover=True))

        md = xhtml_to_markdown(root)
        # Collapse runs of 3+ blank lines to 2
        md = re.sub(r'\n{3,}', '\n\n', md)
        return md.strip()

    def _copy_images(self):
        """Copy images from OEBPS/Images/ to markdown/images/."""
        src = os.path.join(self.oebps, "Images")
        if not os.path.isdir(src):
            # Some books use lowercase
            src = os.path.join(self.oebps, "images")
        if not os.path.isdir(src):
            return
        dst = os.path.join(self.md_dir, "images")
        if os.path.isdir(dst):
            shutil.rmtree(dst)
        shutil.copytree(src, dst)

    def _write_chapter_md(self, filename: str, md: str):
        stem = os.path.splitext(filename)[0]
        out_path = os.path.join(self.md_dir, stem + ".md")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(md)
            f.write("\n")

    def _write_combined_md(self, markdown_map: dict):
        """Write all chapters concatenated into _book.md."""
        out_path = os.path.join(self.md_dir, "_book.md")
        with open(out_path, "w", encoding="utf-8") as f:
            title = self.book_info.get("title", "")
            authors = [a.get("name", "") for a in self.book_info.get("authors", [])]
            f.write("# %s\n\n" % title)
            if authors:
                f.write("*%s*\n\n" % ", ".join(authors))
            f.write("---\n\n")
            for ch in self.chapters:
                filename = ch.get("filename", "")
                md = markdown_map.get(filename)
                if md:
                    f.write(md)
                    f.write("\n\n---\n\n")


# ---------------------------------------------------------------------------
# RagExporter
# ---------------------------------------------------------------------------

class RagExporter:
    """Chunk markdown content by heading boundaries and write JSONL."""

    SOURCE_URL_BASE = "https://learning.oreilly.com/library/view/"

    def __init__(self, book_id: str, book_info: dict, chapters: list,
                 book_path: str, markdown_map: dict = None):
        self.book_id = book_id
        self.book_info = book_info
        self.chapters = chapters
        self.book_path = book_path
        self.markdown_map = markdown_map or {}
        self.oebps = os.path.join(book_path, "OEBPS")

    def export(self, output_path: str):
        """Write chunked JSONL to output_path."""
        title = self.book_info.get("title", "")
        authors = [a.get("name", "") for a in self.book_info.get("authors", [])]
        isbn = self.book_info.get("isbn", "")
        source_url = self.SOURCE_URL_BASE + self.book_id + "/"

        with open(output_path, "w", encoding="utf-8") as f:
            for idx, ch in enumerate(self.chapters):
                filename = ch.get("filename", "")
                ch_title = ch.get("title", "")

                md = self.markdown_map.get(filename)
                if md is None:
                    # Fallback: convert on the fly
                    xhtml_path = os.path.join(self.oebps, filename)
                    if os.path.isfile(xhtml_path):
                        from exporters import MarkdownExporter
                        me = MarkdownExporter(
                            self.book_id, self.book_path, self.book_info, []
                        )
                        md = me._convert_xhtml(xhtml_path)
                    else:
                        continue

                chapter_meta = {
                    "book_id": self.book_id,
                    "title": title,
                    "authors": authors,
                    "isbn": isbn,
                    "chapter_filename": filename,
                    "chapter_title": ch_title,
                    "chapter_index": idx,
                    "source_url": source_url,
                }
                chunks = self._chunk_chapter(md, chapter_meta)
                for chunk in chunks:
                    f.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    def _chunk_chapter(self, md_text: str, chapter_meta: dict) -> list:
        """Split md_text on heading boundaries and return list of chunk dicts."""
        sections = self._split_by_heading(md_text)
        chunks = []
        chunk_idx = 0
        for heading, depth, body in sections:
            # Split oversized sections on paragraph boundaries
            pieces = self._split_by_paragraphs(body, max_tokens=512)
            for piece in pieces:
                piece = piece.strip()
                if not piece:
                    continue
                record = {
                    **chapter_meta,
                    "section_heading": heading,
                    "section_depth": depth,
                    "chunk_index": chunk_idx,
                    "text": piece,
                    "approx_tokens": self._approx_tokens(piece),
                }
                chunks.append(record)
                chunk_idx += 1
        return chunks

    def _split_by_heading(self, md_text: str) -> list:
        """Split markdown on ## / ### heading lines.

        Returns list of (heading, depth, body) tuples.
        depth=0 means preamble before first heading.
        """
        pattern = re.compile(r'^(#{2,3})\s+(.+)$', re.MULTILINE)
        results = []
        pos = 0
        preamble = None

        for m in pattern.finditer(md_text):
            body = md_text[pos:m.start()]
            if preamble is None:
                # Text before first heading
                if body.strip():
                    results.append(("", 0, body))
            else:
                h_text, h_depth = preamble
                results.append((h_text, h_depth, body))
            preamble = (m.group(2).strip(), len(m.group(1)))
            pos = m.end()

        # Remaining text after last heading
        remainder = md_text[pos:]
        if preamble is not None:
            h_text, h_depth = preamble
            results.append((h_text, h_depth, remainder))
        elif remainder.strip():
            results.append(("", 0, remainder))

        return results

    def _split_by_paragraphs(self, text: str, max_tokens: int) -> list:
        """Further split text on paragraph boundaries if over max_tokens."""
        if self._approx_tokens(text) <= max_tokens:
            return [text]
        paragraphs = re.split(r'\n{2,}', text)
        pieces = []
        current = []
        current_tokens = 0
        for para in paragraphs:
            t = self._approx_tokens(para)
            if current_tokens + t > max_tokens and current:
                pieces.append("\n\n".join(current))
                current = [para]
                current_tokens = t
            else:
                current.append(para)
                current_tokens += t
        if current:
            pieces.append("\n\n".join(current))
        return pieces

    @staticmethod
    def _approx_tokens(text: str) -> int:
        return int(len(text.split()) * 1.3)
