# v2 API Fallback Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make safaribooks fall back to the O'Reilly v2 API when the v1 book info endpoint returns 404, so newer books (e.g. ISBN `9781098119058`) can be downloaded.

**Architecture:** `get_book_info()` probes v1 first; on 404 it sets `self.api_v2 = True`, updates `self.api_url` to the v2 base URL, and re-fetches. `get_book_chapters()` and `create_toc()` branch on `self.api_v2` to use the v2 endpoints and normalize responses into the same dict shape the rest of the code already expects.

**Tech Stack:** Python 3.12, requests, pytest (for tests). All changes are in `safaribooks.py`.

---

### Task 1: Add pytest and create test skeleton

**Files:**
- Modify: `requirements.txt`
- Create: `tests/__init__.py`
- Create: `tests/test_v2_normalizers.py`

**Step 1: Add pytest to requirements**

In `requirements.txt`, append:
```
pytest>=8.0.0
```

**Step 2: Create the tests directory**

```bash
mkdir -p tests
touch tests/__init__.py
```

**Step 3: Create the test file with a placeholder test**

Create `tests/test_v2_normalizers.py`:

```python
"""Tests for v2 API response normalizers in SafariBooks."""
import pytest


def test_placeholder():
    """Placeholder — replace in subsequent tasks."""
    assert True
```

**Step 4: Verify pytest runs**

```bash
source .venv/bin/activate && python -m pytest tests/ -v
```

Expected output:
```
tests/test_v2_normalizers.py::test_placeholder PASSED
```

**Step 5: Commit**

```bash
git add requirements.txt tests/
git commit -m "test: add pytest and test skeleton for v2 normalizers"
```

---

### Task 2: Add v2 URL constants

**Files:**
- Modify: `safaribooks.py:31-36` (module-level constants block)

**Step 1: Write the failing test**

Replace `test_placeholder` in `tests/test_v2_normalizers.py`:

```python
from safaribooks import API_V2_TEMPLATE, API_V2_CHAPTERS_TEMPLATE, SAFARI_BASE_HOST


def test_v2_constants_have_correct_host():
    assert SAFARI_BASE_HOST in API_V2_TEMPLATE
    assert SAFARI_BASE_HOST in API_V2_CHAPTERS_TEMPLATE


def test_v2_template_formats_book_id():
    url = API_V2_TEMPLATE.format("9781098119058")
    assert url == "https://learning.oreilly.com/api/v2/epubs/urn:orm:book:9781098119058/"


def test_v2_chapters_template_formats_book_id():
    url = API_V2_CHAPTERS_TEMPLATE.format("9781098119058")
    assert url == "https://learning.oreilly.com/api/v2/epub-chapters/?epub_identifier=urn:orm:book:9781098119058"
```

**Step 2: Run to verify failure**

```bash
source .venv/bin/activate && python -m pytest tests/test_v2_normalizers.py -v
```

Expected: `ImportError: cannot import name 'API_V2_TEMPLATE'`

**Step 3: Add the constants to `safaribooks.py`**

After the existing `SAFARI_BASE_URL`, `API_ORIGIN_URL`, `PROFILE_URL` lines (around line 36), add:

```python
API_V2_TEMPLATE = "https://" + SAFARI_BASE_HOST + "/api/v2/epubs/urn:orm:book:{0}/"
API_V2_CHAPTERS_TEMPLATE = "https://" + SAFARI_BASE_HOST + "/api/v2/epub-chapters/?epub_identifier=urn:orm:book:{0}"
```

**Step 4: Run to verify passing**

```bash
source .venv/bin/activate && python -m pytest tests/test_v2_normalizers.py -v
```

Expected: all 3 tests PASS

**Step 5: Commit**

```bash
git add safaribooks.py tests/test_v2_normalizers.py
git commit -m "feat: add v2 API URL constants"
```

---

### Task 3: Implement `_normalize_v2_book_info()`

**Files:**
- Modify: `safaribooks.py` (add method to `SafariBooks` class, after `get_book_info` around line 617)
- Modify: `tests/test_v2_normalizers.py`

**Step 1: Write the failing test**

Append to `tests/test_v2_normalizers.py`:

```python
from unittest.mock import MagicMock
from safaribooks import SafariBooks, SAFARI_BASE_HOST

# Minimal v2 book info response (as returned by the API)
V2_BOOK_INFO = {
    "ourn": "urn:orm:book:9781098119058",
    "identifier": "9781098119058",
    "isbn": "9781098119065",
    "title": "Designing Data-Intensive Applications, 2nd Edition",
    "publication_date": "2026-02-25",
    "descriptions": {
        "text/plain": "Data is at the center of many challenges.",
        "text/html": "<span>Data is at the center.</span>",
    },
    "tags": ["databases", "distributed-systems"],
    "roughcut": False,
}


def _make_safari_books():
    """Return a SafariBooks instance without running __init__ (avoids network calls)."""
    sb = SafariBooks.__new__(SafariBooks)
    sb.book_id = "9781098119058"
    sb.api_v2 = False
    return sb


def test_normalize_v2_book_info_title():
    sb = _make_safari_books()
    result = sb._normalize_v2_book_info(V2_BOOK_INFO)
    assert result["title"] == "Designing Data-Intensive Applications, 2nd Edition"


def test_normalize_v2_book_info_isbn():
    sb = _make_safari_books()
    result = sb._normalize_v2_book_info(V2_BOOK_INFO)
    assert result["isbn"] == "9781098119065"


def test_normalize_v2_book_info_identifier():
    sb = _make_safari_books()
    result = sb._normalize_v2_book_info(V2_BOOK_INFO)
    assert result["identifier"] == "9781098119058"


def test_normalize_v2_book_info_issued():
    sb = _make_safari_books()
    result = sb._normalize_v2_book_info(V2_BOOK_INFO)
    assert result["issued"] == "2026-02-25"


def test_normalize_v2_book_info_description():
    sb = _make_safari_books()
    result = sb._normalize_v2_book_info(V2_BOOK_INFO)
    assert result["description"] == "Data is at the center of many challenges."


def test_normalize_v2_book_info_web_url():
    sb = _make_safari_books()
    result = sb._normalize_v2_book_info(V2_BOOK_INFO)
    assert "9781098119058" in result["web_url"]
    assert SAFARI_BASE_HOST in result["web_url"]
    assert result["web_url"].endswith("/files/")


def test_normalize_v2_book_info_empty_authors():
    sb = _make_safari_books()
    result = sb._normalize_v2_book_info(V2_BOOK_INFO)
    assert result["authors"] == []


def test_normalize_v2_book_info_subjects_from_tags():
    sb = _make_safari_books()
    result = sb._normalize_v2_book_info(V2_BOOK_INFO)
    assert result["subjects"] == [{"name": "databases"}, {"name": "distributed-systems"}]


def test_normalize_v2_book_info_no_cover_key():
    """cover must be absent so the caller's `if "cover" in self.book_info` is False."""
    sb = _make_safari_books()
    result = sb._normalize_v2_book_info(V2_BOOK_INFO)
    assert "cover" not in result
```

**Step 2: Run to verify failure**

```bash
source .venv/bin/activate && python -m pytest tests/test_v2_normalizers.py -k "normalize_v2_book_info" -v
```

Expected: `AttributeError: '_normalize_v2_book_info'`

**Step 3: Add `_normalize_v2_book_info` to `SafariBooks` in `safaribooks.py`**

Insert after `get_book_info()` (after line 617):

```python
def _normalize_v2_book_info(self, v2: dict) -> dict:
    """Map a v2 book info response to the v1-compatible dict shape."""
    files_base = (
        "https://" + SAFARI_BASE_HOST
        + "/api/v2/epubs/urn:orm:book:" + self.book_id + "/files/"
    )
    return {
        "title": v2.get("title", "n/a"),
        "isbn": v2.get("isbn", ""),
        "identifier": v2.get("identifier", ""),
        "issued": v2.get("publication_date", "n/a"),
        "description": v2.get("descriptions", {}).get("text/plain", ""),
        "web_url": files_base,
        "authors": [],
        "publishers": [],
        "rights": "n/a",
        "subjects": [{"name": t} for t in v2.get("tags", [])],
    }
```

**Step 4: Run to verify passing**

```bash
source .venv/bin/activate && python -m pytest tests/test_v2_normalizers.py -k "normalize_v2_book_info" -v
```

Expected: all 9 tests PASS

**Step 5: Commit**

```bash
git add safaribooks.py tests/test_v2_normalizers.py
git commit -m "feat: add _normalize_v2_book_info()"
```

---

### Task 4: Implement `_normalize_v2_chapter()`

**Files:**
- Modify: `safaribooks.py` (add method after `_normalize_v2_book_info`)
- Modify: `tests/test_v2_normalizers.py`

**Step 1: Write the failing test**

Append to `tests/test_v2_normalizers.py`:

```python
# Minimal v2 chapter object (first result from epub-chapters endpoint)
V2_CHAPTER = {
    "ourn": "urn:orm:book:9781098119058:chapter:cover.html",
    "title": "Cover",
    "content_url": "https://learning.oreilly.com/api/v2/epubs/urn:orm:book:9781098119058/files/cover.html",
    "related_assets": {
        "images": [
            "https://learning.oreilly.com/api/v2/epubs/urn:orm:book:9781098119058/files/assets/cover.png"
        ],
        "stylesheets": [
            "https://learning.oreilly.com/api/v2/epubs/urn:orm:book:9781098119058/files/epub.css"
        ],
        "audio_files": [],
        "fonts": [],
        "html_files": [],
        "other_assets": [],
        "scripts": [],
        "svgs": [],
        "videos": [],
    },
    "indexed_position": 0,
    "is_skippable": True,
}


def test_normalize_v2_chapter_title():
    sb = _make_safari_books()
    result = sb._normalize_v2_chapter(V2_CHAPTER)
    assert result["title"] == "Cover"


def test_normalize_v2_chapter_filename():
    sb = _make_safari_books()
    result = sb._normalize_v2_chapter(V2_CHAPTER)
    assert result["filename"] == "cover.html"


def test_normalize_v2_chapter_content_contains_v2():
    """content URL must contain '/v2/' so existing api_v2_detected logic fires."""
    sb = _make_safari_books()
    result = sb._normalize_v2_chapter(V2_CHAPTER)
    assert "/v2/" in result["content"]
    assert result["content"].endswith("cover.html")


def test_normalize_v2_chapter_asset_base_url():
    sb = _make_safari_books()
    result = sb._normalize_v2_chapter(V2_CHAPTER)
    assert result["asset_base_url"].endswith("/files")
    assert "9781098119058" in result["asset_base_url"]


def test_normalize_v2_chapter_images_are_relative():
    """Images must be relative paths so existing asset_base_url + '/' + img logic works."""
    sb = _make_safari_books()
    result = sb._normalize_v2_chapter(V2_CHAPTER)
    assert result["images"] == ["assets/cover.png"]


def test_normalize_v2_chapter_stylesheets_wrapped():
    """Stylesheets must be [{"url": ...}] dicts to match v1 format."""
    sb = _make_safari_books()
    result = sb._normalize_v2_chapter(V2_CHAPTER)
    assert result["stylesheets"] == [
        {"url": "https://learning.oreilly.com/api/v2/epubs/urn:orm:book:9781098119058/files/epub.css"}
    ]


def test_normalize_v2_chapter_no_images():
    sb = _make_safari_books()
    chapter = {**V2_CHAPTER, "related_assets": {**V2_CHAPTER["related_assets"], "images": []}}
    result = sb._normalize_v2_chapter(chapter)
    assert result["images"] == []
```

**Step 2: Run to verify failure**

```bash
source .venv/bin/activate && python -m pytest tests/test_v2_normalizers.py -k "normalize_v2_chapter" -v
```

Expected: `AttributeError: '_normalize_v2_chapter'`

**Step 3: Add `_normalize_v2_chapter` to `SafariBooks` in `safaribooks.py`**

Insert after `_normalize_v2_book_info`:

```python
def _normalize_v2_chapter(self, v2_ch: dict) -> dict:
    """Map a v2 epub-chapter object to the v1-compatible dict shape."""
    files_base = (
        "https://" + SAFARI_BASE_HOST
        + "/api/v2/epubs/urn:orm:book:" + self.book_id + "/files"
    )
    assets = v2_ch.get("related_assets", {})
    images = [
        img.split("/files/")[-1]
        for img in assets.get("images", [])
        if "/files/" in img
    ]
    stylesheets = [{"url": s} for s in assets.get("stylesheets", [])]
    return {
        "title": v2_ch.get("title", ""),
        "filename": v2_ch["content_url"].split("/")[-1],
        "content": v2_ch["content_url"],
        "asset_base_url": files_base,
        "images": images,
        "stylesheets": stylesheets,
    }
```

**Step 4: Run to verify passing**

```bash
source .venv/bin/activate && python -m pytest tests/test_v2_normalizers.py -k "normalize_v2_chapter" -v
```

Expected: all 7 tests PASS

**Step 5: Commit**

```bash
git add safaribooks.py tests/test_v2_normalizers.py
git commit -m "feat: add _normalize_v2_chapter()"
```

---

### Task 5: Implement `_normalize_v2_toc_entry()`

**Files:**
- Modify: `safaribooks.py` (add static method after `_normalize_v2_chapter`)
- Modify: `tests/test_v2_normalizers.py`

**Step 1: Write the failing test**

Append to `tests/test_v2_normalizers.py`:

```python
from safaribooks import SafariBooks

V2_TOC_ENTRY = {
    "depth": 1,
    "reference_id": "9781098119058-/preface01.html",
    "ourn": "urn:orm:book:9781098119058:chapter:preface01.html",
    "url": "https://learning.oreilly.com/api/v2/epub-chapters/urn:orm:book:9781098119058:chapter:preface01.html/",
    "fragment": "preface",
    "title": "Preface",
    "children": [
        {
            "depth": 2,
            "reference_id": "9781098119058-/preface01.html",
            "ourn": "urn:orm:book:9781098119058:chapter:preface01.html",
            "url": "...",
            "fragment": "id585",
            "title": "Who Should Read This Book?",
            "children": [],
        }
    ],
}

V2_TOC_ENTRY_NO_FRAGMENT = {
    "depth": 1,
    "reference_id": "9781098119058-/cover.html",
    "ourn": "urn:orm:book:9781098119058:chapter:cover.html",
    "url": "...",
    "fragment": "",
    "title": "Cover",
    "children": [],
}


def test_normalize_v2_toc_entry_depth():
    result = SafariBooks._normalize_v2_toc_entry(V2_TOC_ENTRY)
    assert result["depth"] == 1


def test_normalize_v2_toc_entry_fragment():
    result = SafariBooks._normalize_v2_toc_entry(V2_TOC_ENTRY)
    assert result["fragment"] == "preface"


def test_normalize_v2_toc_entry_id_fallback_when_no_fragment():
    """When fragment is empty, id must come from the ourn last segment."""
    result = SafariBooks._normalize_v2_toc_entry(V2_TOC_ENTRY_NO_FRAGMENT)
    assert result["id"] == "cover.html"


def test_normalize_v2_toc_entry_label():
    result = SafariBooks._normalize_v2_toc_entry(V2_TOC_ENTRY)
    assert result["label"] == "Preface"


def test_normalize_v2_toc_entry_href():
    result = SafariBooks._normalize_v2_toc_entry(V2_TOC_ENTRY)
    assert result["href"] == "preface01.html"


def test_normalize_v2_toc_entry_children_normalized():
    result = SafariBooks._normalize_v2_toc_entry(V2_TOC_ENTRY)
    assert len(result["children"]) == 1
    assert result["children"][0]["label"] == "Who Should Read This Book?"
    assert result["children"][0]["depth"] == 2
```

**Step 2: Run to verify failure**

```bash
source .venv/bin/activate && python -m pytest tests/test_v2_normalizers.py -k "normalize_v2_toc" -v
```

Expected: `AttributeError: '_normalize_v2_toc_entry'`

**Step 3: Add `_normalize_v2_toc_entry` to `SafariBooks` in `safaribooks.py`**

Insert after `_normalize_v2_chapter` as a `@staticmethod`:

```python
@staticmethod
def _normalize_v2_toc_entry(entry: dict) -> dict:
    """Map a v2 table-of-contents entry to the v1-compatible dict shape."""
    fragment = entry.get("fragment", "")
    return {
        "depth": entry["depth"],
        "fragment": fragment,
        "id": entry["ourn"].split(":")[-1],
        "label": entry["title"],
        "href": entry["reference_id"].split("-/")[-1],
        "children": [
            SafariBooks._normalize_v2_toc_entry(c)
            for c in entry.get("children", [])
        ],
    }
```

**Step 4: Run to verify passing**

```bash
source .venv/bin/activate && python -m pytest tests/test_v2_normalizers.py -k "normalize_v2_toc" -v
```

Expected: all 6 tests PASS

**Step 5: Commit**

```bash
git add safaribooks.py tests/test_v2_normalizers.py
git commit -m "feat: add _normalize_v2_toc_entry()"
```

---

### Task 6: Wire v2 fallback into `__init__` and `get_book_info()`

**Files:**
- Modify: `safaribooks.py:387-388` (`__init__`, after `self.book_id = args.bookid`)
- Modify: `safaribooks.py:584-617` (`get_book_info`)

**Step 1: Add `self.api_v2 = False` in `__init__`**

In `safaribooks.py`, find (around line 387):
```python
        self.book_id = args.bookid
        self.api_url = self.API_TEMPLATE.format(self.book_id)
```

Change to:
```python
        self.book_id = args.bookid
        self.api_v2 = False
        self.api_url = self.API_TEMPLATE.format(self.book_id)
```

**Step 2: Modify `get_book_info()` to add v2 fallback**

Replace the current `get_book_info` (lines 584–617) with:

```python
def get_book_info(self):
    response = self.requests_provider(self.api_url)
    if response == 0:
        self.display.exit("API: unable to retrieve book info.")

    if response.status_code in (401, 403):
        self.display.exit(
            "API: authentication failed (HTTP %d) — your cookies may have expired. "
            "Please refresh them via the Cookie screen." % response.status_code
        )

    if response.status_code == 404:
        self.display.info("v1 API returned 404, trying v2 API...")
        self.api_v2 = True
        self.api_url = API_V2_TEMPLATE.format(self.book_id)
        response = self.requests_provider(self.api_url)
        if response == 0:
            self.display.exit("API: unable to retrieve book info (v1 and v2 both failed).")
        if response.status_code != 200:
            self.display.exit(
                "API: v2 also returned HTTP %d for book info." % response.status_code
            )

    elif response.status_code != 200:
        self.display.exit("API: unexpected status %d retrieving book info." % response.status_code)

    try:
        response = response.json()
    except ValueError:
        snippet = response.text[:200].replace("\n", " ")
        self.display.exit(
            "API: response was not valid JSON (HTTP %d, %d bytes): %r" % (
                response.status_code, len(response.content), snippet
            )
        )

    if self.api_v2:
        return self._normalize_v2_book_info(response)

    if not isinstance(response, dict) or len(response.keys()) == 1:
        self.display.exit(self.display.api_error(response))

    if "last_chapter_read" in response:
        del response["last_chapter_read"]

    for key, value in response.items():
        if value is None:
            response[key] = 'n/a'

    return response
```

**Step 3: Verify all existing tests still pass**

```bash
source .venv/bin/activate && python -m pytest tests/ -v
```

Expected: all tests PASS (no regressions)

**Step 4: Commit**

```bash
git add safaribooks.py
git commit -m "feat: wire v2 fallback into get_book_info() on HTTP 404"
```

---

### Task 7: Wire v2 into `get_book_chapters()`

**Files:**
- Modify: `safaribooks.py:619-655` (`get_book_chapters`)

**Step 1: Replace `get_book_chapters` with v2-aware version**

Replace the current `get_book_chapters` (lines 619–655) with:

```python
def get_book_chapters(self, page=1, _v2_next_url=None):
    if self.api_v2:
        url = _v2_next_url or API_V2_CHAPTERS_TEMPLATE.format(self.book_id)
    else:
        url = urljoin(self.api_url, "chapter/?page=%s" % page)

    response = self.requests_provider(url)
    if response == 0:
        self.display.exit("API: unable to retrieve book chapters.")

    if response.status_code not in (200, 201):
        self.display.exit(
            "API: unexpected status %d retrieving chapters — "
            "cookies may be expired." % response.status_code
        )

    try:
        response = response.json()
    except ValueError:
        snippet = response.text[:200].replace("\n", " ")
        self.display.exit(
            "API: chapter list response was not valid JSON (HTTP %d, %d bytes): %r" % (
                response.status_code, len(response.content), snippet
            )
        )

    if not isinstance(response, dict) or len(response.keys()) == 1:
        self.display.exit(self.display.api_error(response))

    if "results" not in response or not len(response["results"]):
        self.display.exit("API: unable to retrieve book chapters.")

    if response["count"] > sys.getrecursionlimit():
        sys.setrecursionlimit(response["count"])

    if self.api_v2:
        chapters = [self._normalize_v2_chapter(ch) for ch in response["results"]]
    else:
        chapters = response["results"]

    result = []
    result.extend([c for c in chapters if "cover" in c["filename"] or "cover" in c["title"]])
    for c in result:
        del chapters[chapters.index(c)]
    result += chapters

    if response["next"]:
        if self.api_v2:
            return result + self.get_book_chapters(_v2_next_url=response["next"])
        else:
            return result + self.get_book_chapters(page + 1)
    return result
```

**Step 2: Verify tests still pass**

```bash
source .venv/bin/activate && python -m pytest tests/ -v
```

Expected: all PASS

**Step 3: Commit**

```bash
git add safaribooks.py
git commit -m "feat: wire v2 into get_book_chapters()"
```

---

### Task 8: Wire v2 into `create_toc()`

**Files:**
- Modify: `safaribooks.py:1088-1111` (`create_toc`)

**Step 1: Replace `create_toc` with v2-aware version**

Replace the current `create_toc` (lines 1088–1111) with:

```python
def create_toc(self):
    if self.api_v2:
        toc_url = urljoin(self.api_url, "table-of-contents/")
    else:
        toc_url = urljoin(self.api_url, "toc/")

    response = self.requests_provider(toc_url)
    if response == 0:
        self.display.exit("API: unable to retrieve book TOC. "
                          "Don't delete any files, just run again this program"
                          " in order to complete the `.epub` creation!")

    response = response.json()

    if self.api_v2:
        response = [self._normalize_v2_toc_entry(e) for e in response]
    elif not isinstance(response, list) and len(response.keys()) == 1:
        self.display.exit(
            self.display.api_error(response) +
            " Don't delete any files, just run again this program"
            " in order to complete the `.epub` creation!"
        )

    navmap, _, max_depth = self.parse_toc(response)
    return self.TOC_NCX.format(
        (self.book_info["isbn"] if self.book_info["isbn"] else self.book_id),
        max_depth,
        self.book_title,
        ", ".join(aut.get("name", "") for aut in self.book_info.get("authors", [])),
        navmap
    )
```

**Step 2: Verify tests still pass**

```bash
source .venv/bin/activate && python -m pytest tests/ -v
```

Expected: all PASS

**Step 3: Commit**

```bash
git add safaribooks.py
git commit -m "feat: wire v2 into create_toc()"
```

---

### Task 9: Smoke test with a real v2 book

**Goal:** Confirm `9781098119058` now downloads past the book info stage.

**Step 1: Ensure cookies are fresh**

Open the app (or run `python tui.py`) and verify cookies are valid — the TUI cookie screen should show green. If not, refresh them via the browser flow.

**Step 2: Run safaribooks against the v2 book**

```bash
source .venv/bin/activate
python safaribooks.py 9781098119058
```

**Expected log output (in `info_9781098119058.log`):**

```
[...] Successfully authenticated.
[...] v1 API returned 404, trying v2 API...
[...] Retrieving book info...
[...] Title: Designing Data-Intensive Applications, 2nd Edition
[...] Retrieving book chapters...
[...] [progress output]
```

The run should proceed past "Retrieving book info" and start downloading chapters.

**Step 3: Commit test results note**

No code change needed. If the smoke test passes, proceed to final commit.

---

### Task 10: Final cleanup commit

**Step 1: Run full test suite one last time**

```bash
source .venv/bin/activate && python -m pytest tests/ -v
```

Expected: all PASS

**Step 2: Final commit**

```bash
git add -A
git commit -m "feat: complete v2 API fallback for O'Reilly newer books

Books unavailable on /api/v1/book/{id}/ now automatically retry
against /api/v2/epubs/urn:orm:book:{id}/ with full field mapping
for book info, chapters, and table of contents.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```
