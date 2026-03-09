# SafariBooks (tbdtechpro fork)

Download and generate *EPUB* files from your [O'Reilly Learning](https://learning.oreilly.com) library subscription.

> **Note:** This is a maintained fork of [lorenzodifuccia/safaribooks](https://github.com/lorenzodifuccia/safaribooks).
> Credit and thanks to Lorenzo Di Fuccia for the original implementation.
> This fork adds email/password login, a v2 API fallback, an interactive TUI, and several export features.

*For personal and educational use only. Please read the O'Reilly [Terms of Service](https://learning.oreilly.com/terms/) before use.*

---

## Overview

- [Requirements & Setup](#requirements--setup)
- [Authentication](#authentication)
- [Usage — CLI](#usage--cli)
- [Usage — Interactive TUI](#usage--interactive-tui)
- [Export Features](#export-features)
- [Calibre EPUB Conversion](#calibre-epub-conversion)
- [Examples](#examples)

---

## Requirements & Setup

**Python 3.11+** is required. Run the included setup script to create a virtual environment and install all dependencies:

```bash
git clone https://github.com/tbdtechpro/safaribooks.git
cd safaribooks/
chmod +x setup.sh
./setup.sh
```

The setup script:
1. Verifies your platform (targets Ubuntu 24.04, warns on others)
2. Installs system packages via `apt-get` (Python 3, build tools, Calibre)
3. Creates a `.venv` virtual environment
4. Installs Python dependencies (`lxml`, `requests`, `browser_cookie3`, `bubbletea`, `lipgloss`)

After setup, activate the environment:

```bash
source .venv/bin/activate
```

**Python dependencies** (see `requirements.txt`):
```
lxml>=4.9.0
requests>=2.28.0
browser_cookie3
```
Plus `bubbletea` and `lipgloss` (tbdtechpro forks) for the TUI.

---

## Authentication

This fork supports two authentication methods:

### Option 1 — Email & Password (recommended)

Pass credentials directly on the command line:

```bash
python safaribooks.py --cred "account@example.com:MyPassword" BOOKID
```

Or use `--login` to be prompted interactively (safer — password not visible in shell history):

```bash
python safaribooks.py --login BOOKID
```

Session cookies are saved to `cookies.json` automatically. On subsequent runs you can omit credentials until the session expires.

### Option 2 — Cookie from Browser (SSO / company login)

If you authenticate via SSO, Google, or a company portal, log in via your browser, then copy the session cookie string and save it:

```bash
# From DevTools → Network → any request → Copy "Cookie" header value
python retrieve_cookies.py --cookie "orm-jwt=eyJ...; orm-rt=..."

# Or paste interactively
python retrieve_cookies.py --cookie
```

The TUI also has a dedicated cookie input screen.

```bash
# Auto-extract from browser (may not work on modern Chrome due to OS encryption)
python retrieve_cookies.py
```

> **Security note:** Anyone with access to `cookies.json` can use your session. Use `--no-cookies` if on a shared machine.

---

## Usage — CLI

```bash
python safaribooks.py [OPTIONS] <BOOK ID>
```

The Book ID is the number in the O'Reilly URL:
`https://learning.oreilly.com/library/view/book-name/XXXXXXXXXXXXX/`

### All Options

```
usage: safaribooks.py [--cred <EMAIL:PASS> | --login] [--no-cookies]
                      [--kindle] [--preserve-log]
                      [--skip-if-downloaded] [--scan-library]
                      [--export-markdown] [--export-db] [--export-rag]
                      [--help]
                      <BOOK ID>

positional arguments:
  <BOOK ID>             Book ID from the O'Reilly URL.

options:
  --cred <EMAIL:PASS>   Email and password for login.
  --login               Prompt for credentials interactively.
  --no-cookies          Do not save session to cookies.json.
  --kindle              Add CSS rules for Kindle compatibility
                        (blocks overflow on table/pre elements).
  --preserve-log        Keep the info log file even on success.
  --skip-if-downloaded  Skip download if the book is already in
                        the local library registry (library.db).
  --scan-library        Scan existing Books/ directories and
                        populate library.db, then exit.
  --export-markdown     Write GFM Markdown to Books/{title}/markdown/.
  --export-db           Store chapter XHTML and TOC in library.db.
  --export-rag          Write heading-chunked JSONL to
                        Books/{title}/rag/{book_id}_rag.jsonl.
                        Implies --export-db.
  --help                Show this help message.
```

---

## Usage — Interactive TUI

Launch the terminal UI for a menu-driven experience:

```bash
python tui.py
```

The TUI provides:
- **Login** — email/password login flow
- **Set Cookie** — paste your browser session cookie
- **Add Book to Queue** — queue multiple books for batch download
- **View / Run Queue** — manage the queue with export toggles:
  - `m` — toggle Markdown export
  - `d` — toggle Content DB storage
  - `x` — toggle RAG JSONL export
  - `k` — toggle skip-if-downloaded
  - `r` — run all downloads

---

## Export Features

All outputs land in `Books/{book-title}/` alongside the EPUB.

### Library Registry (`library.db`)

Every download is automatically recorded in `Books/library.db` (SQLite). Fields include title, authors, ISBN, sha256 of the EPUB, chapter count, and API version used.

```bash
# Populate registry from existing Books/ directories (no network needed)
python safaribooks.py --scan-library

# Skip re-downloading a book already in the registry
python safaribooks.py --skip-if-downloaded BOOKID

# Inspect the registry
sqlite3 Books/library.db "SELECT title, chapter_count, downloaded_at FROM registry"
```

### Markdown Export (`--export-markdown`)

Converts each chapter's XHTML to [GitHub Flavored Markdown](https://github.github.com/gfm/):

```
Books/{title}/
└── markdown/
    ├── images/       (copied from OEBPS/Images/)
    ├── ch01.md
    ├── ch02.md
    └── _book.md      (all chapters combined)
```

Handles headings, code blocks, tables, lists, links, images, figures, and O'Reilly-specific `data-type` elements.

### Content DB (`--export-db`)

Stores raw XHTML and converted Markdown for every chapter, plus a flattened TOC, in `library.db`:

```bash
sqlite3 Books/library.db ".tables"
# registry  chapters  toc

sqlite3 Books/library.db "SELECT title, markdown_text FROM chapters WHERE book_id='BOOKID' LIMIT 1"
```

### RAG JSONL Export (`--export-rag`)

Produces a heading-chunked JSONL file for use with retrieval-augmented generation (RAG) pipelines. Each record is a chunk with full provenance:

```json
{
  "book_id": "9781098166298",
  "title": "AI Engineering",
  "authors": ["Chip Huyen"],
  "chapter_filename": "ch01.xhtml",
  "chapter_title": "Introduction",
  "section_heading": "From Language Models to LLMs",
  "section_depth": 2,
  "chunk_index": 0,
  "text": "...",
  "approx_tokens": 487,
  "source_url": "https://learning.oreilly.com/library/view/..."
}
```

Output path: `Books/{title}/rag/{book_id}_rag.jsonl`

`--export-rag` implies `--export-db`.

### Full Export Example

```bash
python safaribooks.py \
  --cred "account@example.com:password" \
  --export-markdown \
  --export-db \
  --export-rag \
  9781098166298
```

---

## Calibre EPUB Conversion

The generated EPUB is a raw extraction. For best E-Reader compatibility, convert with [Calibre](https://calibre-ebook.com/):

```bash
ebook-convert "Books/My Book (9781234567890)/9781234567890.epub" \
              "Books/My Book (9781234567890)/9781234567890_clean.epub"
```

Or use the included helper (converts all EPUBs in your Books directory):

```bash
python calibre_convert.py Books/*/*.epub
```

For Kindle, use `--kindle` when downloading, then convert to AZW3 or MOBI:

```bash
python safaribooks.py --kindle BOOKID
# Then in Calibre: select "Ignore margins" in conversion options
```

---

## Examples

### Basic download

```bash
python safaribooks.py --cred "my@email.com:MyPassword" 9781491958698
```

Output:
```
[-] Logging into O'Reilly...
[*] Retrieving book info...
[-] Title: Test-Driven Development with Python, 2nd Edition
[-] Authors: Harry J.W. Percival
[-] Identifier: 9781491958698
[*] Retrieving book chapters...
[-] Downloading book contents... (53 chapters)
    [####################################################] 100%
[-] Downloading book CSSs... (2 files)
[-] Downloading book images... (142 files)
[-] Creating EPUB file...
[*] Done: Books/Test-Driven Development with Python 2nd Edition (9781491958698)/9781491958698.epub
```

### Skip already-downloaded books

```bash
python safaribooks.py --cred "my@email.com:MyPassword" --skip-if-downloaded 9781491958698
# Book already downloaded: Test-Driven Development with Python, 2nd Edition
# EPUB: Books/Test-Driven Development with Python 2nd Edition (9781491958698)/9781491958698.epub
```

### Kindle-friendly export

```bash
python safaribooks.py --kindle 9781491958698
```

---

## API Version Support

This fork automatically falls back to the O'Reilly v2 API when a book is unavailable on the v1 endpoint. Newer books (published 2024+) often require the v2 API — this is handled transparently with no extra configuration needed.

---

## Credits

- Original project: [lorenzodifuccia/safaribooks](https://github.com/lorenzodifuccia/safaribooks) by Lorenzo Di Fuccia
- This fork: [tbdtechpro/safaribooks](https://github.com/tbdtechpro/safaribooks)

For issues with this fork, please open an issue on the [tbdtechpro/safaribooks](https://github.com/tbdtechpro/safaribooks/issues) repository.
