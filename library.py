"""
library.py — BookRegistry: SQLite-backed download registry + content storage.

Database location: Books/library.db (created automatically).
"""

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone


_BOOK_DIR_RE = re.compile(r'^.+\((\w+)\)$')


class BookRegistry:
    """Manages a SQLite registry of downloaded books, chapters, and TOC data."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self.ensure_schema()

    def ensure_schema(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS registry (
                book_id       TEXT PRIMARY KEY,
                title         TEXT,
                authors       TEXT,
                isbn          TEXT,
                issued        TEXT,
                publishers    TEXT,
                subjects      TEXT,
                description   TEXT,
                epub_path     TEXT,
                book_dir      TEXT,
                epub_sha256   TEXT,
                downloaded_at TEXT,
                chapter_count INTEGER,
                api_version   TEXT
            );

            CREATE TABLE IF NOT EXISTS chapters (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id       TEXT NOT NULL,
                chapter_index INTEGER NOT NULL,
                filename      TEXT,
                title         TEXT,
                xhtml_content TEXT,
                markdown_text TEXT,
                FOREIGN KEY (book_id) REFERENCES registry(book_id)
            );

            CREATE TABLE IF NOT EXISTS toc (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id     TEXT NOT NULL,
                depth       INTEGER,
                label       TEXT,
                href        TEXT,
                fragment    TEXT,
                parent_id   INTEGER,
                play_order  INTEGER,
                FOREIGN KEY (book_id) REFERENCES registry(book_id)
            );
        """)
        self._conn.commit()

    def close(self):
        self._conn.close()

    # ------------------------------------------------------------------
    # Feature 1 — registry
    # ------------------------------------------------------------------

    def is_downloaded(self, book_id: str) -> bool:
        """Return True if book is in registry AND epub still exists on disk."""
        row = self._conn.execute(
            "SELECT epub_path FROM registry WHERE book_id = ?", (book_id,)
        ).fetchone()
        if row is None:
            return False
        return bool(row["epub_path"]) and os.path.isfile(row["epub_path"])

    def get_epub_path(self, book_id: str):
        row = self._conn.execute(
            "SELECT epub_path FROM registry WHERE book_id = ?", (book_id,)
        ).fetchone()
        return row["epub_path"] if row else None

    def get_title(self, book_id: str):
        row = self._conn.execute(
            "SELECT title FROM registry WHERE book_id = ?", (book_id,)
        ).fetchone()
        return row["title"] if row else None

    def record_download(self, book_info: dict, epub_path: str, book_dir: str,
                        chapters: list, api_version: str):
        """Insert or replace registry row after a successful download."""
        sha256 = _sha256_file(epub_path) if os.path.isfile(epub_path) else None
        self._conn.execute(
            """
            INSERT OR REPLACE INTO registry
                (book_id, title, authors, isbn, issued, publishers, subjects,
                 description, epub_path, book_dir, epub_sha256, downloaded_at,
                 chapter_count, api_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                book_info.get("identifier") or book_info.get("isbn", ""),
                book_info.get("title", ""),
                json.dumps([a.get("name", "") for a in book_info.get("authors", [])]),
                book_info.get("isbn", ""),
                book_info.get("issued", ""),
                json.dumps([p.get("name", "") for p in book_info.get("publishers", [])]),
                json.dumps([s.get("name", "") for s in book_info.get("subjects", [])]),
                book_info.get("description", ""),
                epub_path,
                book_dir,
                sha256,
                datetime.now(timezone.utc).isoformat(),
                len(chapters),
                api_version,
            )
        )
        self._conn.commit()

    def scan_existing_books(self, books_dir: str) -> int:
        """Retroactively populate registry from existing Books/ directories.

        Walks books_dir looking for subdirectories matching the pattern
        ``{title} ({book_id})``.  For each valid directory it:
        - Verifies the epub exists
        - Parses content.opf for metadata (via stdlib xml.etree)
        - Counts *.xhtml files in OEBPS/
        - Computes epub sha256
        - Inserts into registry (skips if already present)

        Returns the number of books inserted.
        """
        import xml.etree.ElementTree as ET

        inserted = 0
        if not os.path.isdir(books_dir):
            return 0

        for entry in os.scandir(books_dir):
            if not entry.is_dir():
                continue
            m = _BOOK_DIR_RE.match(entry.name)
            if not m:
                continue
            book_id = m.group(1)
            book_dir = entry.path

            epub_path = os.path.join(book_dir, book_id + ".epub")
            if not os.path.isfile(epub_path):
                continue

            # Skip if already in registry
            existing = self._conn.execute(
                "SELECT 1 FROM registry WHERE book_id = ?", (book_id,)
            ).fetchone()
            if existing:
                continue

            # Parse metadata from content.opf
            opf_path = os.path.join(book_dir, "OEBPS", "content.opf")
            title, authors, isbn, issued, publishers, subjects, description = (
                "", "[]", "", "", "[]", "[]", ""
            )
            if os.path.isfile(opf_path):
                try:
                    tree = ET.parse(opf_path)
                    ns = {
                        "dc": "http://purl.org/dc/elements/1.1/",
                        "opf": "http://www.idpf.org/2007/opf",
                    }
                    meta = tree.find("opf:metadata", ns)
                    if meta is not None:
                        title = _text(meta.find("dc:title", ns))
                        isbn = _text(meta.find("dc:identifier", ns))
                        issued = _text(meta.find("dc:date", ns))
                        description = _text(meta.find("dc:description", ns))
                        authors = json.dumps([
                            el.text for el in meta.findall("dc:creator", ns) if el.text
                        ])
                        publishers = json.dumps([
                            el.text for el in meta.findall("dc:publisher", ns) if el.text
                        ])
                        subjects = json.dumps([
                            el.text for el in meta.findall("dc:subject", ns) if el.text
                        ])
                except ET.ParseError:
                    pass

            # Count xhtml chapters
            oebps = os.path.join(book_dir, "OEBPS")
            chapter_count = 0
            if os.path.isdir(oebps):
                chapter_count = sum(
                    1 for f in os.listdir(oebps) if f.endswith(".xhtml")
                )

            sha256 = _sha256_file(epub_path)
            downloaded_at = datetime.fromtimestamp(
                os.path.getmtime(epub_path), tz=timezone.utc
            ).isoformat()

            self._conn.execute(
                """
                INSERT INTO registry
                    (book_id, title, authors, isbn, issued, publishers, subjects,
                     description, epub_path, book_dir, epub_sha256, downloaded_at,
                     chapter_count, api_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (book_id, title, authors, isbn, issued, publishers, subjects,
                 description, epub_path, book_dir, sha256, downloaded_at,
                 chapter_count, "unknown")
            )
            inserted += 1

        self._conn.commit()
        return inserted

    # ------------------------------------------------------------------
    # Feature 3 — content DB
    # ------------------------------------------------------------------

    def store_chapters(self, book_id: str, chapters: list, book_path: str,
                       markdown_map: dict = None):
        """Read XHTML from disk and store in chapters table.

        chapters: list of chapter dicts (with 'filename' and 'title' keys).
        book_path: root book directory (OEBPS/ will be appended).
        markdown_map: optional {filename: markdown_str} from a MarkdownExporter run.
        """
        # Delete existing rows for this book first (idempotent re-run)
        self._conn.execute("DELETE FROM chapters WHERE book_id = ?", (book_id,))

        oebps = os.path.join(book_path, "OEBPS")
        for idx, ch in enumerate(chapters):
            filename = ch.get("filename", "")
            xhtml_content = ""
            xhtml_path = os.path.join(oebps, filename)
            if os.path.isfile(xhtml_path):
                try:
                    with open(xhtml_path, encoding="utf-8", errors="replace") as f:
                        xhtml_content = f.read()
                except OSError:
                    pass

            md = (markdown_map or {}).get(filename, None)

            self._conn.execute(
                """
                INSERT INTO chapters
                    (book_id, chapter_index, filename, title, xhtml_content, markdown_text)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (book_id, idx, filename, ch.get("title", ""), xhtml_content, md)
            )

        self._conn.commit()

    def store_toc(self, book_id: str, toc_data: list):
        """Flatten TOC tree and store in toc table."""
        self._conn.execute("DELETE FROM toc WHERE book_id = ?", (book_id,))
        play_order = [0]
        self._flatten_toc(book_id, toc_data, parent_id=None, play_order=play_order)
        self._conn.commit()

    def _flatten_toc(self, book_id: str, entries: list, parent_id, play_order: list):
        for entry in entries:
            play_order[0] += 1
            cursor = self._conn.execute(
                """
                INSERT INTO toc
                    (book_id, depth, label, href, fragment, parent_id, play_order)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    book_id,
                    entry.get("depth", 0),
                    entry.get("label", ""),
                    entry.get("href", ""),
                    entry.get("fragment", ""),
                    parent_id,
                    play_order[0],
                )
            )
            row_id = cursor.lastrowid
            children = entry.get("children", [])
            if children:
                self._flatten_toc(book_id, children, parent_id=row_id,
                                  play_order=play_order)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _text(el) -> str:
    return el.text if el is not None and el.text else ""


# ------------------------------------------------------------------
# EPUB content parser (no download required)
# ------------------------------------------------------------------

def parse_epub_contents(book_dir: str) -> tuple:
    """Parse an existing EPUB directory and return (book_info, chapters, toc_data).

    Reads OEBPS/content.opf for metadata and spine order, and OEBPS/toc.ncx
    for hierarchical chapter titles.

    Returns:
        book_info  — dict compatible with MarkdownExporter / RagExporter
        chapters   — list of {"filename": str, "title": str} in spine order
        toc_data   — nested list of TOC entry dicts for BookRegistry.store_toc()

    Raises FileNotFoundError if content.opf is missing.
    """
    import xml.etree.ElementTree as ET

    OPF_NS  = "http://www.idpf.org/2007/opf"
    DC_NS   = "http://purl.org/dc/elements/1.1/"
    NCX_NS  = "http://www.daisy.org/z3986/2005/ncx/"

    opf_path = os.path.join(book_dir, "OEBPS", "content.opf")
    if not os.path.isfile(opf_path):
        raise FileNotFoundError(f"content.opf not found in {book_dir}")

    tree = ET.parse(opf_path)
    root = tree.getroot()

    # ── Metadata ─────────────────────────────────────────────────────────────
    meta = root.find(f"{{{OPF_NS}}}metadata")
    if meta is None:
        meta = root.find("metadata")  # no-namespace fallback

    def _dc(tag):
        el = None
        if meta is not None:
            el = meta.find(f"{{{DC_NS}}}{tag}")
        return el.text.strip() if el is not None and el.text else ""

    def _dc_all(tag):
        if meta is None:
            return []
        return [el.text.strip() for el in meta.findall(f"{{{DC_NS}}}{tag}") if el.text]

    title      = _dc("title")
    isbn       = _dc("identifier")
    issued     = _dc("date")
    description= _dc("description")
    authors    = _dc_all("creator")
    publishers = _dc_all("publisher")
    subjects   = _dc_all("subject")

    book_info = {
        "title":       title,
        "authors":     [{"name": a} for a in authors],
        "isbn":        isbn,
        "issued":      issued,
        "description": description,
        "publishers":  publishers,
        "subjects":    subjects,
    }

    # ── Manifest: item id → href ──────────────────────────────────────────────
    manifest = {}
    for item in root.findall(f".//{{{OPF_NS}}}item"):
        item_id    = item.get("id", "")
        href       = item.get("href", "")
        media_type = item.get("media-type", "")
        if href.endswith(".xhtml") and media_type == "application/xhtml+xml":
            manifest[item_id] = href

    # ── Spine: ordered list of hrefs ─────────────────────────────────────────
    spine_hrefs = []
    for itemref in root.findall(f".//{{{OPF_NS}}}itemref"):
        idref = itemref.get("idref", "")
        if idref in manifest:
            spine_hrefs.append(manifest[idref])

    # ── NCX TOC: filename → display title (first top-level entry per file) ───
    file_title: dict = {}
    toc_data: list = []

    ncx_path = os.path.join(book_dir, "OEBPS", "toc.ncx")
    if os.path.isfile(ncx_path):
        try:
            ncx_tree = ET.parse(ncx_path)
            ncx_root = ncx_tree.getroot()
            nav_map  = ncx_root.find(f"{{{NCX_NS}}}navMap")
            if nav_map is not None:
                toc_data = _parse_ncx_navmap(nav_map, NCX_NS, file_title, depth=0)
        except ET.ParseError:
            pass

    # ── Build chapters list ───────────────────────────────────────────────────
    chapters = []
    for href in spine_hrefs:
        # Use NCX title if available, otherwise derive from filename stem
        default_title = os.path.splitext(os.path.basename(href))[0].replace("_", " ")
        chapters.append({
            "filename": href,
            "title":    file_title.get(href, default_title),
        })

    return book_info, chapters, toc_data


def _parse_ncx_navmap(nav_map, NCX_NS: str, file_title: dict, depth: int) -> list:
    """Recursively parse NCX navPoints into a nested list of TOC entry dicts."""
    entries = []
    for nav_point in nav_map.findall(f"{{{NCX_NS}}}navPoint"):
        label_el  = nav_point.find(f"{{{NCX_NS}}}navLabel/{{{NCX_NS}}}text")
        content_el = nav_point.find(f"{{{NCX_NS}}}content")

        label = label_el.text.strip() if label_el is not None and label_el.text else ""
        src   = content_el.get("src", "") if content_el is not None else ""

        # src may include a fragment:  "Chapter_1.xhtml#anchor"
        filename, _, fragment = src.partition("#")

        # Record the first title seen for each file (top-most nav entry = chapter title)
        if filename and filename not in file_title:
            file_title[filename] = label

        children = _parse_ncx_navmap(nav_point, NCX_NS, file_title, depth + 1)
        entries.append({
            "depth":    depth,
            "label":    label,
            "href":     filename,
            "fragment": fragment,
            "children": children,
        })
    return entries
