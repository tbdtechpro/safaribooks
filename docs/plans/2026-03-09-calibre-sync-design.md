# Calibre Library Sync — Design

**Date:** 2026-03-09
**Feature:** TUI screen to identify unsynced local books and add them to the Calibre library

---

## Goal

Allow the user to compare locally downloaded books (in `Books/`) against their Calibre library,
review what is missing, and add selected books via `calibredb add` — all from within the KeroOle TUI.

---

## Architecture

New `Screen.CALIBRE_SYNC` with three internal phases controlled by state flags (no sub-screens).

### Phases

1. **Scanning** — spinner while background worker queries calibredb and scans Books/
2. **Review** — checklist of unsynced books; user toggles selection before committing
3. **Adding** — per-book progress list as `calibredb add` runs

### New workers

- `CalibreSyncWorker` — runs the scan and comparison, sends `CalibreSyncDoneMsg`
- `CalibreAddWorker` — runs `calibredb add` per selected book, sends progress/done messages

---

## Matching Logic

Primary source: `calibredb list --fields title,authors,identifiers --for-machine`
(calibredb may output multiple JSON array chunks; parse each independently and merge)

For each local book (parsed via `parse_epub_contents`):

| Check | Result |
|---|---|
| ISBN present on both sides and matches | **Definitive match** → hidden from review |
| Normalized title+author matches, no ISBN confirmation | **Ambiguous match** → dimmed, unchecked, labelled `~ possible match` |
| No match on either check | **Not in Calibre** → checked, ready to add |

Normalization: lowercase, strip punctuation (`[^a-z0-9\s]`), collapse whitespace.

Books with no `.epub` file in their folder are silently skipped (count shown in summary line).

---

## Review Screen Layout

```
  Calibre Sync — 47 not in library  (115 synced · 3 ambiguous · 2 skipped)

  [✓] Clean Code — Robert C. Martin
  [✓] Fluent Python 2nd Ed — Luciano Ramalho
  [~] Python Cookbook — David Beazley          ← ~ possible match (dimmed, unchecked)
  ...

  ↑/↓  move    Space  toggle    a  select all    r  add selected    Esc  back
```

- Definitive matches: hidden entirely
- Ambiguous matches: shown dimmed, unchecked, not auto-selected
- Unsynced books: shown normal weight, pre-checked

---

## Adding Phase

Reuses the same progress-list pattern as the existing calibre conversion screen.
Per-book status: `⟳ Adding…` / `✓ Added` / `✗ Error message`.
Individual failures do not abort the run; continue with remaining books.
Footer shows `Esc  back to menu    q  quit` when all done.

---

## Error Handling

| Condition | Behaviour |
|---|---|
| `calibredb` not on PATH | Show error on screen entry with install hint |
| `calibredb list` non-zero exit | Show stderr in error style, offer Esc back |
| `calibredb add` failure (single book) | Mark failed, continue with rest |
| No EPUB file for a local book | Skip silently, include in "skipped" count |
| Chunked JSON from calibredb | Parse each array independently and concatenate |

---

## New State Fields (AppModel)

```python
sync_scanning: bool = False
sync_results: list  = []   # list of SyncEntry(book_id, title, author, epub_path, match)
sync_selected: set  = set()
sync_cursor: int    = 0
sync_scroll: int    = 0
sync_adding: bool   = False
sync_add_status: dict = {}  # book_id → "adding" | "done" | "error:msg"
sync_all_done: bool = False
```

## New Message Types

```python
CalibreSyncDoneMsg(entries: list, already_synced: int, skipped: int)
CalibreAddProgressMsg(book_id: str, stage: str)
CalibreAddDoneMsg()
```
