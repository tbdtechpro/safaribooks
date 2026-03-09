# Calibre Library Sync — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a TUI screen that compares locally downloaded books against the user's Calibre library, shows what's missing, and adds selected books via `calibredb add`.

**Architecture:** New `calibre_sync.py` module handles all matching logic (testable in isolation). `tui.py` gets a new `Screen.CALIBRE_SYNC` with three internal phases: scanning, review (checklist), and adding. Two new background workers (`CalibreSyncWorker`, `CalibreAddWorker`) follow the same thread+message pattern as existing workers.

**Tech Stack:** Python stdlib (`subprocess`, `re`, `json`, `dataclasses`), existing `library.parse_epub_contents`, existing `bubbletea` TUI framework, `calibredb` CLI.

---

## Task 1: `calibre_sync.py` — matching logic

**Files:**
- Create: `calibre_sync.py`
- Create: `tests/test_calibre_sync.py`

**Step 1: Write the failing tests**

Create `tests/test_calibre_sync.py`:

```python
"""Tests for Calibre sync matching logic."""
import pytest
from calibre_sync import normalize_for_match, parse_calibredb_output, match_books, SyncEntry

# --- normalize_for_match ---

def test_normalize_lowercases():
    assert normalize_for_match("Python Cookbook") == "python cookbook"

def test_normalize_strips_punctuation():
    assert normalize_for_match("Clean Code: A Handbook") == "clean code a handbook"

def test_normalize_collapses_whitespace():
    assert normalize_for_match("Fluent  Python") == "fluent python"

def test_normalize_strips_edition_noise():
    # 2nd edition, 3rd ed etc should still match; we don't strip them but normalise
    assert normalize_for_match("Fluent Python, 2nd Edition") == "fluent python 2nd edition"

# --- parse_calibredb_output ---

SINGLE_ARRAY = '[{"id":1,"title":"Clean Code","authors":"Robert C. Martin","identifiers":{"isbn":"9780132350884"}}]'
DOUBLE_ARRAY = (
    '[{"id":1,"title":"Clean Code","authors":"Robert C. Martin","identifiers":{"isbn":"9780132350884"}}]'
    '\n'
    '[{"id":2,"title":"Fluent Python","authors":"Luciano Ramalho","identifiers":{}}]'
)

def test_parse_single_array():
    result = parse_calibredb_output(SINGLE_ARRAY)
    assert len(result) == 1
    assert result[0]["title"] == "Clean Code"

def test_parse_double_array():
    result = parse_calibredb_output(DOUBLE_ARRAY)
    assert len(result) == 2
    titles = {r["title"] for r in result}
    assert titles == {"Clean Code", "Fluent Python"}

def test_parse_empty_string():
    assert parse_calibredb_output("") == []

def test_parse_invalid_json_returns_empty():
    assert parse_calibredb_output("not json") == []

# --- match_books ---

LOCAL_BOOKS = [
    # ISBN match
    {"book_id": "111", "title": "Clean Code", "authors": [{"name": "Robert C. Martin"}],
     "isbn": "9780132350884", "epub_path": "/books/111/book.epub"},
    # title+author match only (no ISBN on local side)
    {"book_id": "222", "title": "Fluent Python", "authors": [{"name": "Luciano Ramalho"}],
     "isbn": "", "epub_path": "/books/222/book.epub"},
    # no match
    {"book_id": "333", "title": "Designing Data-Intensive Applications",
     "authors": [{"name": "Martin Kleppmann"}],
     "isbn": "9781449373320", "epub_path": "/books/333/book.epub"},
    # no epub
    {"book_id": "444", "title": "Some Book", "authors": [{"name": "Author"}],
     "isbn": "", "epub_path": ""},
]

CALIBRE_BOOKS = [
    {"title": "Clean Code", "authors": "Robert C. Martin",
     "identifiers": {"isbn": "9780132350884"}},
    {"title": "Fluent Python", "authors": "Luciano Ramalho", "identifiers": {}},
]

def test_isbn_match_is_definitive():
    entries = match_books(LOCAL_BOOKS, CALIBRE_BOOKS)
    entry = next(e for e in entries if e.book_id == "111")
    assert entry.match == "definitive"

def test_title_author_match_is_ambiguous():
    entries = match_books(LOCAL_BOOKS, CALIBRE_BOOKS)
    entry = next(e for e in entries if e.book_id == "222")
    assert entry.match == "ambiguous"

def test_no_match_is_none():
    entries = match_books(LOCAL_BOOKS, CALIBRE_BOOKS)
    entry = next(e for e in entries if e.book_id == "333")
    assert entry.match == "none"

def test_no_epub_is_skipped():
    entries = match_books(LOCAL_BOOKS, CALIBRE_BOOKS)
    ids = [e.book_id for e in entries]
    assert "444" not in ids

def test_match_count():
    # 3 valid epub entries (444 skipped)
    entries = match_books(LOCAL_BOOKS, CALIBRE_BOOKS)
    assert len(entries) == 3
```

**Step 2: Run tests to confirm they fail**

```bash
cd /home/matt/github/safaribooks
python -m pytest tests/test_calibre_sync.py -v 2>&1 | head -30
```
Expected: `ImportError` (module doesn't exist yet).

**Step 3: Implement `calibre_sync.py`**

Create `calibre_sync.py`:

```python
"""
calibre_sync.py — Logic for comparing local SafariBooks downloads against a Calibre library.

Public API:
  parse_calibredb_output(raw)  → list of calibre book dicts
  normalize_for_match(text)    → normalized string for comparison
  match_books(local, calibre)  → list of SyncEntry
  run_calibredb_list()         → (raw_output, error_str)
"""

import json
import re
import subprocess
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class SyncEntry:
    book_id: str
    title: str
    author: str
    epub_path: str
    match: str   # "none" | "ambiguous" | "definitive"


def normalize_for_match(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_calibredb_output(raw: str) -> list:
    """Parse calibredb --for-machine output, which may be multiple JSON arrays."""
    if not raw.strip():
        return []
    results = []
    for match in re.finditer(r"\[.*?\]", raw, re.DOTALL):
        try:
            chunk = json.loads(match.group())
            if isinstance(chunk, list):
                results.extend(chunk)
        except json.JSONDecodeError:
            continue
    return results


def run_calibredb_list() -> tuple:
    """
    Run `calibredb list` and return (raw_output, error).
    Returns ("", error_message) on failure.
    """
    try:
        result = subprocess.run(
            ["calibredb", "list", "--fields", "title,authors,identifiers", "--for-machine"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            return "", result.stderr.strip() or "calibredb list failed"
        return result.stdout, ""
    except FileNotFoundError:
        return "", "calibredb not found — is Calibre installed?"
    except subprocess.TimeoutExpired:
        return "", "calibredb timed out after 60 seconds"


def _book_isbn(book_info: dict) -> str:
    """Extract ISBN from local book_info dict."""
    return (book_info.get("isbn") or "").strip()


def _calibre_isbn(calibre_book: dict) -> str:
    """Extract ISBN from a calibredb list entry."""
    return (calibre_book.get("identifiers") or {}).get("isbn", "").strip()


def _first_author(book_info: dict) -> str:
    authors = book_info.get("authors") or []
    if not authors:
        return ""
    return authors[0].get("name", "") if isinstance(authors[0], dict) else str(authors[0])


def match_books(local_books: list, calibre_books: list) -> List[SyncEntry]:
    """
    Compare local books against calibre library.

    local_books: list of dicts with keys: book_id, title, authors, isbn, epub_path
    calibre_books: list of dicts from parse_calibredb_output

    Returns SyncEntry list; books with empty epub_path are skipped.
    """
    # Build calibre lookup sets
    calibre_isbns = set()
    calibre_title_author = set()
    for cb in calibre_books:
        isbn = _calibre_isbn(cb)
        if isbn:
            calibre_isbns.add(isbn)
        title = normalize_for_match(cb.get("title") or "")
        author = normalize_for_match(cb.get("authors") or "")
        if title:
            calibre_title_author.add((title, author))

    entries = []
    for lb in local_books:
        epub_path = (lb.get("epub_path") or "").strip()
        if not epub_path:
            continue

        isbn = _book_isbn(lb)
        title = normalize_for_match(lb.get("title") or "")
        author = normalize_for_match(_first_author(lb))

        if isbn and isbn in calibre_isbns:
            match = "definitive"
        elif title and (title, author) in calibre_title_author:
            match = "ambiguous"
        else:
            match = "none"

        entries.append(SyncEntry(
            book_id=lb["book_id"],
            title=lb.get("title") or lb["book_id"],
            author=_first_author(lb),
            epub_path=epub_path,
            match=match,
        ))

    return entries
```

**Step 4: Run tests**

```bash
python -m pytest tests/test_calibre_sync.py -v
```
Expected: all tests pass.

**Step 5: Commit**

```bash
git add calibre_sync.py tests/test_calibre_sync.py
git commit -m "feat: add calibre_sync matching logic with tests"
```

---

## Task 2: New message types and `Screen.CALIBRE_SYNC` enum

**Files:**
- Modify: `tui.py`

**Step 1: Add Screen enum value**

Find the `Screen` enum (search for `class Screen`) and add:
```python
CALIBRE_SYNC = auto()
```

**Step 2: Add message dataclasses**

After the existing message dataclasses (near `BookDoneMsg`, `AllDownloadsDoneMsg` etc.), add:

```python
@dataclass
class CalibreSyncDoneMsg(tea.Msg):
    entries: list          # list of SyncEntry
    already_synced: int    # count of definitive matches (hidden)
    skipped: int           # count of books with no EPUB

@dataclass
class CalibreAddProgressMsg(tea.Msg):
    book_id: str
    stage: str             # "adding" | "done" | error message

@dataclass
class CalibreAddDoneMsg(tea.Msg):
    pass
```

**Step 3: Add state fields to `AppModel.__init__`**

Inside `__init__`, after the `settings_*` fields, add:

```python
# calibre sync screen
self.sync_scanning: bool       = False
self.sync_entries: list        = []   # list of SyncEntry (non-definitive only)
self.sync_already_synced: int  = 0
self.sync_skipped: int         = 0
self.sync_selected: set        = set()
self.sync_cursor: int          = 0
self.sync_scroll: int          = 0
self.sync_adding: bool         = False
self.sync_add_status: dict     = {}   # book_id → "adding"|"done"|"error:msg"
self.sync_all_done: bool       = False
```

**Step 4: Register key/view handlers**

In `_key_dispatch` (the dict mapping Screen → handler), add:
```python
Screen.CALIBRE_SYNC: self._key_calibre_sync,
```

In `_view_dispatch` (the dict mapping Screen → view), add:
```python
Screen.CALIBRE_SYNC: self._view_calibre_sync,
```

**Step 5: Add menu item**

Find `MENU_ITEMS` list and add before "Export Paths / Settings":
```python
("Sync with Calibre Library", Screen.CALIBRE_SYNC),
```

**Step 6: Wire menu navigation**

In `_key_main`, the existing code does `self.screen = target` for menu items. The CALIBRE_SYNC screen needs to auto-start the scan when entered. Find the `_key_main` handler and add:

```python
if target == Screen.CALIBRE_SYNC:
    self._start_calibre_sync()
    return self, None
```

(before the generic `self.screen = target` assignment)

**Step 7: Verify syntax**

```bash
python3 -c "import ast; ast.parse(open('tui.py').read()); print('OK')"
```

**Step 8: Commit**

```bash
git add tui.py
git commit -m "feat: scaffold Screen.CALIBRE_SYNC enum, messages, state, menu item"
```

---

## Task 3: `CalibreSyncWorker` — background scan

**Files:**
- Modify: `tui.py`

**Step 1: Add `CalibreSyncWorker` class**

Add after `CalibreWorker` class (search for `class CalibreWorker`):

```python
class CalibreSyncWorker:
    """Scans Books/ and calibredb to find unsynced books."""

    def __init__(self, books_dir: str, program: tea.Program):
        self.books_dir = books_dir
        self.program   = program
        self._thread   = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def _run(self):
        import re as _re
        from calibre_sync import run_calibredb_list, parse_calibredb_output, match_books
        from library import parse_epub_contents

        # 1. Query calibredb
        raw, err = run_calibredb_list()
        if err:
            self.program.send(CalibreSyncDoneMsg(entries=[], already_synced=0, skipped=0,
                                                  error=err))
            return
        calibre_books = parse_calibredb_output(raw)

        # 2. Scan Books/
        _dir_re = _re.compile(r'^.+\((\w+)\)$')
        local_books = []
        for entry in sorted(os.scandir(self.books_dir), key=lambda e: e.name):
            if not entry.is_dir():
                continue
            m = _dir_re.match(entry.name)
            if not m:
                continue
            book_id  = m.group(1)
            book_dir = entry.path

            # Find EPUB
            epub_path = ""
            for f in os.listdir(book_dir):
                if f.endswith(".epub"):
                    epub_path = os.path.join(book_dir, f)
                    break

            try:
                book_info, _, _ = parse_epub_contents(book_dir)
            except Exception:
                book_info = {}

            local_books.append({
                "book_id":   book_id,
                "title":     book_info.get("title") or book_id,
                "authors":   book_info.get("authors") or [],
                "isbn":      book_info.get("isbn") or "",
                "epub_path": epub_path,
            })

        # 3. Match
        entries    = match_books(local_books, calibre_books)
        definitive = [e for e in entries if e.match == "definitive"]
        visible    = [e for e in entries if e.match != "definitive"]
        skipped    = sum(1 for lb in local_books if not lb["epub_path"])

        self.program.send(CalibreSyncDoneMsg(
            entries=visible,
            already_synced=len(definitive),
            skipped=skipped,
        ))
```

Note: `CalibreSyncDoneMsg` needs an `error: str = ""` field — update the dataclass from Task 2:
```python
@dataclass
class CalibreSyncDoneMsg(tea.Msg):
    entries: list
    already_synced: int
    skipped: int
    error: str = ""
```

**Step 2: Add `_start_calibre_sync` method to `AppModel`**

```python
def _start_calibre_sync(self):
    self.sync_scanning    = True
    self.sync_entries     = []
    self.sync_selected    = set()
    self.sync_cursor      = 0
    self.sync_scroll      = 0
    self.sync_adding      = False
    self.sync_add_status  = {}
    self.sync_all_done    = False
    self.screen           = Screen.CALIBRE_SYNC
    worker = CalibreSyncWorker(
        books_dir=os.path.join(PATH, "Books"),
        program=self._program,
    )
    worker.start()
```

**Step 3: Handle `CalibreSyncDoneMsg` in `update()`**

In the `update()` method, after existing message handlers, add:

```python
if isinstance(msg, CalibreSyncDoneMsg):
    self.sync_scanning = False
    if msg.error:
        self.sync_error = msg.error
    else:
        self.sync_entries      = msg.entries
        self.sync_already_synced = msg.already_synced
        self.sync_skipped      = msg.skipped
        # Pre-select all "none" match books
        self.sync_selected = {
            e.book_id for e in msg.entries if e.match == "none"
        }
    return self, None
```

Also add `self.sync_error: str = ""` to `AppModel.__init__` state fields.

**Step 4: Verify syntax**

```bash
python3 -c "import ast; ast.parse(open('tui.py').read()); print('OK')"
```

**Step 5: Commit**

```bash
git add tui.py
git commit -m "feat: add CalibreSyncWorker and scan trigger"
```

---

## Task 4: Review screen — view + key handler

**Files:**
- Modify: `tui.py`

**Step 1: Add `_view_calibre_sync` method**

Add after `_view_settings`:

```python
def _view_calibre_sync(self) -> str:
    lines = [self._header("Sync with Calibre Library"), ""]

    if self.sync_scanning:
        lines.append("")
        lines.append(hint_style.render("  Scanning library and calibredb…"))
        lines.append("")
        lines.append(self._footer("Esc  cancel"))
        return panel_style.width(min(self.width - 4, 72)).render("\n".join(lines))

    if self.sync_error:
        lines.append(error_style.render(f"  ✗ {self.sync_error}"))
        lines.append("")
        lines.append(self._footer("Esc  back"))
        return panel_style.width(min(self.width - 4, 72)).render("\n".join(lines))

    if self.sync_adding or self.sync_all_done:
        return self._view_calibre_sync_adding()

    # ── Review phase ────────────────────────────────────────────────────────
    entries    = self.sync_entries
    unsynced   = [e for e in entries if e.match == "none"]
    ambiguous  = [e for e in entries if e.match == "ambiguous"]
    selected   = len([e for e in unsynced if e.book_id in self.sync_selected])

    summary = (
        f"  {len(unsynced)} not in library  "
        f"({self.sync_already_synced} synced"
        + (f" · {len(ambiguous)} ambiguous" if ambiguous else "")
        + (f" · {self.sync_skipped} skipped" if self.sync_skipped else "")
        + ")"
    )
    lines.append(accent_style.render(summary))
    lines.append(hint_style.render(f"  {selected} selected for import"))
    lines.append("")

    if not entries:
        lines.append(success_style.render("  ✓ All local books are already in your Calibre library!"))
        lines.append("")
        lines.append(self._footer("Esc  back"))
        return panel_style.width(min(self.width - 4, 72)).render("\n".join(lines))

    rows_available = max(3, self.height - 8)
    visible_count  = max(1, rows_available // 2)
    total          = len(entries)
    self.sync_scroll = max(0, min(self.sync_scroll, max(0, total - visible_count)))
    visible = entries[self.sync_scroll: self.sync_scroll + visible_count]

    for i, entry in enumerate(visible):
        abs_idx   = self.sync_scroll + i
        focused   = abs_idx == self.sync_cursor
        checked   = entry.book_id in self.sync_selected
        is_ambig  = entry.match == "ambiguous"

        if is_ambig:
            box  = hint_style.render("[~]")
            mark = hint_style.render(f"  {entry.title[:42]}  ~ possible match")
        else:
            box  = accent_style.render("[✓]") if checked else "[ ]"
            label = f"  {entry.title[:42]}"
            if entry.author:
                label += hint_style.render(f" — {entry.author[:28]}")
            mark = label

        prefix = "▶ " if focused else "  "
        row = f"{prefix}{box}{mark}"
        if focused:
            lines.append(cursor_style.render(row))
        else:
            lines.append(row)
        lines.append("")

    if total > visible_count:
        end = min(self.sync_scroll + visible_count, total)
        lines.append(hint_style.render(f"  Showing {self.sync_scroll + 1}–{end} of {total}   ↑/↓ to scroll"))
        lines.append("")

    lines.append(self._footer("↑/↓  move    Space  toggle    a  all    r  add selected    Esc  back"))
    return panel_style.width(min(self.width - 4, 72)).render("\n".join(lines))


def _view_calibre_sync_adding(self) -> str:
    lines = [self._header("Adding to Calibre Library"), ""]

    for book_id, status in self.sync_add_status.items():
        entry = next((e for e in self.sync_entries if e.book_id == book_id), None)
        label = entry.title[:40] if entry else book_id
        if status == "adding":
            st = Style().foreground(C_YELLOW).render("⟳ Adding…")
        elif status == "done":
            st = success_style.render("✓ Added")
        elif status.startswith("error:"):
            st = error_style.render("✗ " + status[6:50])
        else:
            st = hint_style.render(status)
        lines.append(f"  {label}")
        lines.append(f"  {st}")
        lines.append("")

    if self.sync_all_done:
        lines.append(success_style.render("  All done!"))
        lines.append("")
        lines.append(self._footer("Esc  back to menu    q  quit"))
    else:
        lines.append(self._footer("Running…"))

    return panel_style.width(min(self.width - 4, 72)).render("\n".join(lines))
```

**Step 2: Add `_key_calibre_sync` method**

```python
def _key_calibre_sync(self, key: str):
    # Scanning phase — only Esc works
    if self.sync_scanning:
        if key == "escape":
            self.screen = Screen.MAIN
        return self, None

    # Adding complete — Esc/q to exit
    if self.sync_all_done:
        if key in ("escape", "q"):
            self.screen = Screen.MAIN
        return self, None

    # Adding in progress — no keys
    if self.sync_adding:
        return self, None

    # Error state
    if self.sync_error:
        if key == "escape":
            self.screen = Screen.MAIN
        return self, None

    # Review phase
    entries = self.sync_entries
    total   = len(entries)

    if key == "escape":
        self.screen = Screen.MAIN
        return self, None

    if key in ("up", "k"):
        self.sync_cursor = max(0, self.sync_cursor - 1)
        # Scroll up if needed
        if self.sync_cursor < self.sync_scroll:
            self.sync_scroll = self.sync_cursor
    elif key in ("down", "j"):
        self.sync_cursor = min(total - 1, self.sync_cursor + 1)

    elif key == " ":
        if 0 <= self.sync_cursor < total:
            entry = entries[self.sync_cursor]
            if entry.match == "none":  # ambiguous not toggleable
                if entry.book_id in self.sync_selected:
                    self.sync_selected.discard(entry.book_id)
                else:
                    self.sync_selected.add(entry.book_id)

    elif key == "a":
        none_ids = {e.book_id for e in entries if e.match == "none"}
        if self.sync_selected >= none_ids:
            self.sync_selected.clear()
        else:
            self.sync_selected = none_ids.copy()

    elif key == "r":
        to_add = [e for e in entries if e.book_id in self.sync_selected]
        if to_add:
            self._start_calibre_add(to_add)

    return self, None
```

**Step 3: Verify syntax**

```bash
python3 -c "import ast; ast.parse(open('tui.py').read()); print('OK')"
```

**Step 4: Commit**

```bash
git add tui.py
git commit -m "feat: add calibre sync review screen view and key handler"
```

---

## Task 5: `CalibreAddWorker` — add phase

**Files:**
- Modify: `tui.py`

**Step 1: Add `CalibreAddWorker` class**

Add after `CalibreSyncWorker`:

```python
class CalibreAddWorker:
    """Runs `calibredb add` for each selected SyncEntry."""

    def __init__(self, entries: list, program: tea.Program):
        self.entries = entries
        self.program = program
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def _run(self):
        for entry in self.entries:
            self.program.send(CalibreAddProgressMsg(entry.book_id, "adding"))
            try:
                result = subprocess.run(
                    ["calibredb", "add", entry.epub_path],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if result.returncode == 0:
                    self.program.send(CalibreAddProgressMsg(entry.book_id, "done"))
                else:
                    err = (result.stderr or result.stdout or "unknown error").strip()[:80]
                    self.program.send(CalibreAddProgressMsg(entry.book_id, f"error:{err}"))
            except subprocess.TimeoutExpired:
                self.program.send(CalibreAddProgressMsg(entry.book_id, "error:timed out"))
            except Exception as exc:
                self.program.send(CalibreAddProgressMsg(entry.book_id, f"error:{exc}"))

        self.program.send(CalibreAddDoneMsg())
```

**Step 2: Add `_start_calibre_add` method to `AppModel`**

```python
def _start_calibre_add(self, entries: list):
    self.sync_adding     = True
    self.sync_all_done   = False
    self.sync_add_status = {e.book_id: "queued" for e in entries}
    worker = CalibreAddWorker(entries=entries, program=self._program)
    worker.start()
```

**Step 3: Handle `CalibreAddProgressMsg` and `CalibreAddDoneMsg` in `update()`**

```python
if isinstance(msg, CalibreAddProgressMsg):
    self.sync_add_status[msg.book_id] = msg.stage
    return self, None

if isinstance(msg, CalibreAddDoneMsg):
    self.sync_adding   = False
    self.sync_all_done = True
    return self, None
```

**Step 4: Verify syntax**

```bash
python3 -c "import ast; ast.parse(open('tui.py').read()); print('OK')"
```

**Step 5: Run existing tests to confirm nothing broken**

```bash
python -m pytest tests/ -v
```
Expected: all existing tests pass.

**Step 6: Commit**

```bash
git add tui.py
git commit -m "feat: add CalibreAddWorker and add-phase message handlers"
```

---

## Task 6: Manual smoke test

**Step 1: Launch TUI**
```bash
python tui.py
```

**Step 2: Navigate to "Sync with Calibre Library"**
- Use ↑/↓ to highlight, Enter to select
- Confirm spinner appears while scanning

**Step 3: Review screen**
- Confirm summary line shows correct counts
- Confirm pre-checked books are unsynced ones
- Confirm ambiguous matches are dimmed and unchecked
- Toggle a book with Space, confirm checkbox changes
- Press `a` twice — confirm selects all then deselects all

**Step 4: Dry run (don't add yet)**
- Press Esc to return to main menu
- Confirm no calibredb changes were made

**Step 5: Add a book**
- Re-enter sync screen, select one book, press `r`
- Confirm adding phase shows `⟳ Adding…` then `✓ Added`
- Confirm `Esc` returns to main menu after completion
