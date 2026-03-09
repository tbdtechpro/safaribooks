"""Tests for Calibre sync matching logic."""
import pytest
from calibre_sync import normalize_for_match, parse_calibredb_output, match_books, SyncEntry

def test_normalize_lowercases():
    assert normalize_for_match("Python Cookbook") == "python cookbook"

def test_normalize_strips_punctuation():
    assert normalize_for_match("Clean Code: A Handbook") == "clean code a handbook"

def test_normalize_collapses_whitespace():
    assert normalize_for_match("Fluent  Python") == "fluent python"

def test_normalize_handles_edition_text():
    assert normalize_for_match("Fluent Python, 2nd Edition") == "fluent python 2nd edition"

NESTED_ARRAY = '[{"id":1,"title":"Tagged Book","authors":"Some Author","identifiers":{"isbn":"9780001234567"},"tags":["python","web"]}]'

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

def test_parse_nested_arrays_in_object():
    result = parse_calibredb_output(NESTED_ARRAY)
    assert len(result) == 1
    assert result[0]["title"] == "Tagged Book"

LOCAL_BOOKS = [
    {"book_id": "111", "title": "Clean Code", "authors": [{"name": "Robert C. Martin"}],
     "isbn": "9780132350884", "epub_path": "/books/111/book.epub"},
    {"book_id": "222", "title": "Fluent Python", "authors": [{"name": "Luciano Ramalho"}],
     "isbn": "", "epub_path": "/books/222/book.epub"},
    {"book_id": "333", "title": "Designing Data-Intensive Applications",
     "authors": [{"name": "Martin Kleppmann"}],
     "isbn": "9781449373320", "epub_path": "/books/333/book.epub"},
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
    entries = match_books(LOCAL_BOOKS, CALIBRE_BOOKS)
    assert len(entries) == 3

LOCAL_HYPHENATED_ISBN = [
    {"book_id": "555", "title": "Hyphenated Book", "authors": [{"name": "Author"}],
     "isbn": "978-0-13-235088-4", "epub_path": "/books/555/book.epub"},
]
CALIBRE_PLAIN_ISBN = [
    {"title": "Hyphenated Book", "authors": "Author", "identifiers": {"isbn": "9780132350884"}},
]

def test_isbn_hyphenation_matches():
    entries = match_books(LOCAL_HYPHENATED_ISBN, CALIBRE_PLAIN_ISBN)
    assert len(entries) == 1
    assert entries[0].match == "definitive"
