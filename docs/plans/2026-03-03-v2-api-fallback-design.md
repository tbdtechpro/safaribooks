# Design: O'Reilly v2 API Fallback

**Date:** 2026-03-03
**Status:** Approved

## Problem

Newer O'Reilly books (e.g. `9781098119058`) return HTTP 404 from the v1 book info
endpoint (`/api/v1/book/{id}/`). The process exits immediately with no fallback,
making these books undownloadable even with valid credentials.

## Approach

Option C — upfront probe. `get_book_info()` acts as the probe point: on a 404 from
v1 it sets `self.api_v2 = True`, switches `self.api_url` to the v2 base URL, and
re-fetches. All downstream methods branch on `self.api_v2`. No extra network
round-trip — the probe is the book info fetch.

## New Constants

```python
API_V2_TEMPLATE          = SAFARI_BASE_URL + "/api/v2/epubs/urn:orm:book:{0}/"
API_V2_CHAPTERS_TEMPLATE = SAFARI_BASE_URL + "/api/v2/epub-chapters/?epub_identifier=urn:orm:book:{0}"
```

## Field Mappings

### Book info (v2 → v1-compatible)

| v1 field     | v2 source                                              |
|--------------|--------------------------------------------------------|
| `title`      | `title`                                                |
| `isbn`       | `isbn`                                                 |
| `identifier` | `identifier`                                           |
| `issued`     | `publication_date`                                     |
| `description`| `descriptions["text/plain"]`                           |
| `web_url`    | constructed: `.../api/v2/epubs/urn:orm:book:{id}/files/` |
| `authors`    | `[]` (absent from v2 response)                         |
| `publishers` | `[]`                                                   |
| `rights`     | `"n/a"`                                                |
| `subjects`   | `[{"name": t} for t in tags]`                          |
| `cover`      | omitted — cover arrives via chapter list               |

### Chapters (v2 → v1-compatible)

| v1 field        | v2 source                                                    |
|-----------------|--------------------------------------------------------------|
| `title`         | `title`                                                      |
| `filename`      | last segment of `content_url` (e.g. `"cover.html"`)         |
| `content`       | `content_url` (contains `/v2/`, triggers existing detection) |
| `asset_base_url`| `.../api/v2/epubs/urn:orm:book:{id}/files`                   |
| `images`        | `related_assets.images` stripped to relative path after `/files/` |
| `stylesheets`   | `[{"url": s} for s in related_assets.stylesheets]`           |

The existing `api_v2_detected` branch in `get()` fires automatically because
`content_url` contains `/v2/` — no changes needed to the HTML download loop.

### TOC entries (v2 → v1-compatible)

| v1 field   | v2 source                                              |
|------------|--------------------------------------------------------|
| `depth`    | `depth`                                                |
| `fragment` | `fragment`                                             |
| `id`       | last segment of `ourn` (fallback when `fragment` empty)|
| `label`    | `title`                                                |
| `href`     | `reference_id.split("-/")[-1]` → `"preface01.html"`   |
| `children` | recursively normalized                                 |

## Methods Changed

| Method               | Change                                                              |
|----------------------|---------------------------------------------------------------------|
| `SafariBooks.__init__`| Add `self.api_v2 = False`                                          |
| `get_book_info()`    | On 404, set `api_v2=True`, update `api_url`, re-fetch, normalize   |
| `get_book_chapters()`| On `api_v2`, use `API_V2_CHAPTERS_TEMPLATE`; follow `next` URL directly; normalize each chapter |
| `create_toc()`       | On `api_v2`, use `table-of-contents/` path; normalize entries      |

## New Private Helpers

- `_normalize_v2_book_info(v2: dict) -> dict`
- `_normalize_v2_chapter(v2_ch: dict) -> dict`
- `_normalize_v2_toc_entry(entry: dict) -> dict` (static)

## Limitations

- `authors`, `publishers`, `rights` will be empty/n/a for v2 books (not present in v2 API response).
- Cover image arrives through the chapter list (as `cover.html`) rather than a
  dedicated `cover` field — existing cover detection by filename handles this.
