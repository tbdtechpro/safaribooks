#!/usr/bin/env python3
"""
SafariBooks TUI — interactive terminal interface for downloading O'Reilly books.

Requires:
  pip install git+https://github.com/tbdtechpro/bubbletea
  pip install git+https://github.com/tbdtechpro/lipgloss
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, List, Optional, Tuple

import bubbletea as tea
import lipgloss
from lipgloss import (
    Color,
    Style,
    join_horizontal,
    join_vertical,
    normal_border,
    rounded_border,
    Center,
    Left,
    Top,
)

from safaribooks import COOKIES_FILE, PATH, SafariBooks, SafariBooksError
from retrieve_cookies import parse_cookie_string, get_oreilly_cookies_from_browser, login_with_credentials

# ── Colours ──────────────────────────────────────────────────────────────────

C_ACCENT   = Color("#7C3AED")   # violet
C_GREEN    = Color("#22C55E")
C_RED      = Color("#EF4444")
C_YELLOW   = Color("#EAB308")
C_MUTED    = Color("#6B7280")
C_WHITE    = Color("#F9FAFB")
C_BG_DARK  = Color("#1F2937")
C_SELECTED = Color("#DDD6FE")   # light violet

# ── Shared styles ─────────────────────────────────────────────────────────────

title_style = (
    Style()
    .bold(True)
    .foreground(C_WHITE)
    .background(C_ACCENT)
    .padding(0, 2)
)

panel_style = (
    Style()
    .border(rounded_border())
    .border_foreground(C_ACCENT)
    .padding(1, 2)
)

hint_style   = Style().foreground(C_MUTED).italic(True)
error_style  = Style().foreground(C_RED).bold(True)
success_style = Style().foreground(C_GREEN).bold(True)
label_style  = Style().foreground(C_MUTED)
value_style  = Style().foreground(C_WHITE)
accent_style = Style().foreground(C_ACCENT).bold(True)
cursor_style = Style().foreground(C_SELECTED).bold(True)

# ── Screens ───────────────────────────────────────────────────────────────────

class Screen(Enum):
    MAIN       = auto()
    LOGIN      = auto()
    COOKIE     = auto()
    ADD_BOOK   = auto()
    QUEUE      = auto()
    DOWNLOAD   = auto()
    CALIBRE      = auto()
    SETTINGS     = auto()
    CALIBRE_SYNC = auto()


# ── Custom messages ───────────────────────────────────────────────────────────

@dataclass
class ProgressMsg(tea.Msg):
    book_id: str
    stage: str
    percent: float   # 0.0–1.0, or -1.0 for stage-only update


@dataclass
class BookDoneMsg(tea.Msg):
    book_id: str
    title: str
    epub_path: str


@dataclass
class BookErrorMsg(tea.Msg):
    book_id: str
    error: str


@dataclass
class AllDownloadsDoneMsg(tea.Msg):
    pass


@dataclass
class CalibreMsg(tea.Msg):
    book_id: str
    stage: str    # "converting" | "done" | "error"
    message: str = ""


@dataclass
class AllCalibreDoneMsg(tea.Msg):
    pass


@dataclass
class CalibreSyncDoneMsg(tea.Msg):
    entries: list          # list of SyncEntry
    already_synced: int    # count of definitive matches (hidden from review)
    skipped: int           # count of books with no EPUB
    error: str = ""

@dataclass
class CalibreAddProgressMsg(tea.Msg):
    book_id: str
    stage: str             # "adding" | "done" | "error:..."

@dataclass
class CalibreAddDoneMsg(tea.Msg):
    pass


@dataclass
class LoginResultMsg(tea.Msg):
    cookies: dict
    error: str = ""


@dataclass
class BrowserCookieMsg(tea.Msg):
    cookies: dict
    error: str = ""


@dataclass
class ClipboardMsg(tea.Msg):
    text: str


# ── Download state per book ───────────────────────────────────────────────────

@dataclass
class BookState:
    book_id: str
    title: str = ""
    stage: str = "Queued"
    percent: float = 0.0
    epub_path: str = ""
    calibre_path: str = ""
    error: str = ""
    done: bool = False
    failed: bool = False
    calibre_done: bool = False
    calibre_failed: bool = False


# ── Progress bar helper ───────────────────────────────────────────────────────

def render_bar(percent: float, width: int = 28) -> str:
    filled = int(percent * width)
    bar = "█" * filled + "░" * (width - filled)
    pct = int(percent * 100)
    filled_style  = Style().foreground(C_ACCENT)
    empty_style   = Style().foreground(C_MUTED)
    filled_str = filled_style.render("█" * filled)
    empty_str  = empty_style.render("░" * (width - filled))
    pct_str    = Style().foreground(C_WHITE if percent < 1.0 else C_GREEN).render(f" {pct:3d}%")
    return filled_str + empty_str + pct_str


# ── Download worker ───────────────────────────────────────────────────────────

class DownloadWorker:
    """Runs book downloads sequentially in a background thread."""

    def __init__(self, book_ids: List[str], program: tea.Program, kindle: bool = False,
                 export_markdown: bool = False, export_db: bool = False,
                 export_rag: bool = False, skip_if_downloaded: bool = False):
        self.book_ids = book_ids
        self.program = program
        self.kindle = kindle
        self.export_markdown = export_markdown
        self.export_db = export_db
        self.export_rag = export_rag
        self.skip_if_downloaded = skip_if_downloaded
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def _run(self):
        for book_id in self.book_ids:
            self.program.send(ProgressMsg(book_id, "Starting…", 0.0))
            try:
                args = argparse.Namespace(
                    bookid=book_id,
                    cred=None,
                    login=False,
                    no_cookies=False,
                    kindle=self.kindle,
                    log=False,
                    export_markdown=self.export_markdown,
                    export_db=self.export_db,
                    export_rag=self.export_rag,
                    skip_if_downloaded=self.skip_if_downloaded,
                    scan_library=False,
                )

                def cb(stage: str, percent: float, _id=book_id):
                    self.program.send(ProgressMsg(_id, stage, percent))

                sb = SafariBooks(args, progress_callback=cb, raise_on_exit=True, quiet=True)
                self.program.send(BookDoneMsg(book_id, sb.book_title, sb.epub_path))

            except SafariBooksError as exc:
                self.program.send(BookErrorMsg(book_id, str(exc)))

            except Exception as exc:
                self.program.send(BookErrorMsg(book_id, f"Unexpected error: {exc}"))

        self.program.send(AllDownloadsDoneMsg())


# ── Export-library worker ─────────────────────────────────────────────────────

class ExportLibraryWorker:
    """Runs markdown/db/rag exports against existing Books/ downloads in a background thread."""

    def __init__(self, books_dir: str, book_ids: List[str], program: tea.Program,
                 export_markdown: bool = False, export_db: bool = False,
                 export_rag: bool = False):
        self.books_dir      = books_dir
        self.book_ids       = book_ids   # pre-scanned list in display order
        self.program        = program
        self.export_markdown = export_markdown
        self.export_db      = export_db
        self.export_rag     = export_rag
        if export_rag:
            self.export_db = True
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def _run(self):
        import re as _re
        from config import load_export_config, book_folder_name
        from library import BookRegistry, parse_epub_contents
        books_dir = self.books_dir

        exp_cfg = load_export_config()
        db_path = exp_cfg.resolved_db_path() or os.path.join(books_dir, "library.db")
        reg     = BookRegistry(db_path)

        _dir_re = _re.compile(r'^.+\((\w+)\)$')

        for entry in sorted(os.scandir(books_dir), key=lambda e: e.name):
            if not entry.is_dir():
                continue
            m = _dir_re.match(entry.name)
            if not m:
                continue
            book_id  = m.group(1)
            book_dir = entry.path

            self.program.send(ProgressMsg(book_id, "Parsing EPUB…", 0.05))
            try:
                book_info, chapters, toc_data = parse_epub_contents(book_dir)
            except FileNotFoundError:
                self.program.send(BookErrorMsg(book_id, "content.opf not found"))
                continue
            except Exception as exc:
                self.program.send(BookErrorMsg(book_id, str(exc)))
                continue

            title  = book_info.get("title") or book_id
            folder = book_folder_name(book_info, book_id, exp_cfg.folder_name_style)

            try:
                markdown_map = None
                if self.export_markdown:
                    self.program.send(ProgressMsg(book_id, "Exporting Markdown…", 0.3))
                    from exporters import MarkdownExporter
                    md_output_dir = exp_cfg.resolved_markdown_dir()
                    exporter = MarkdownExporter(
                        book_id=book_id,
                        book_path=book_dir,
                        book_info=book_info,
                        chapters=chapters,
                        output_dir=md_output_dir,
                        folder_name=folder,
                    )
                    markdown_map = exporter.export()

                if self.export_db:
                    self.program.send(ProgressMsg(book_id, "Storing in DB…", 0.7))
                    reg.store_chapters(
                        book_id=book_id,
                        chapters=chapters,
                        book_path=book_dir,
                        markdown_map=markdown_map,
                    )
                    if toc_data:
                        reg.store_toc(book_id, toc_data)

                if self.export_rag:
                    self.program.send(ProgressMsg(book_id, "Exporting RAG JSONL…", 0.9))
                    from exporters import RagExporter
                    rag_base = exp_cfg.resolved_rag_dir() or os.path.join(book_dir, "rag")
                    os.makedirs(rag_base, exist_ok=True)
                    output_path = os.path.join(rag_base, folder + "_rag.jsonl")
                    exporter = RagExporter(
                        book_id=book_id,
                        book_info=book_info,
                        chapters=chapters,
                        book_path=book_dir,
                        markdown_map=markdown_map,
                    )
                    exporter.export(output_path)

                self.program.send(BookDoneMsg(book_id, title, ""))
            except Exception as exc:
                self.program.send(BookErrorMsg(book_id, str(exc)))

        reg.close()
        self.program.send(AllDownloadsDoneMsg())


# ── Calibre worker ────────────────────────────────────────────────────────────

class CalibreWorker:
    """Runs calibre ebook-convert on each downloaded EPUB in a background thread."""

    def __init__(self, books: List[BookState], program: tea.Program):
        self.books = books
        self.program = program
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def _run(self):
        for book in self.books:
            if not book.epub_path or not os.path.isfile(book.epub_path):
                self.program.send(CalibreMsg(book.book_id, "error", "EPUB not found"))
                continue

            self.program.send(CalibreMsg(book.book_id, "converting"))
            out_path = book.epub_path.replace(".epub", "_calibre.epub")
            try:
                result = subprocess.run(
                    [
                        "ebook-convert",
                        book.epub_path,
                        out_path,
                        "--no-default-epub-cover",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=600,
                )
                if result.returncode == 0:
                    self.program.send(CalibreMsg(book.book_id, "done", out_path))
                else:
                    err = (result.stderr or result.stdout or "unknown error").strip()
                    self.program.send(CalibreMsg(book.book_id, "error", err[:200]))
            except FileNotFoundError:
                self.program.send(CalibreMsg(book.book_id, "error", "`ebook-convert` not found — is Calibre installed?"))
            except subprocess.TimeoutExpired:
                self.program.send(CalibreMsg(book.book_id, "error", "Calibre conversion timed out"))
            except Exception as exc:
                self.program.send(CalibreMsg(book.book_id, "error", str(exc)))

        self.program.send(AllCalibreDoneMsg())


# ── App model ─────────────────────────────────────────────────────────────────

class AppModel(tea.Model):

    MENU_ITEMS = [
        ("Extract Cookies from Browser", "BROWSER"),
        ("Set Session Cookie (paste)",   Screen.COOKIE),
        ("Login with Email/Password",    Screen.LOGIN),
        ("Add Book to Queue",            Screen.ADD_BOOK),
        ("View / Run Queue",             Screen.QUEUE),
        ("Sync with Calibre Library",     Screen.CALIBRE_SYNC),
        ("Export Paths / Settings",      Screen.SETTINGS),
        ("Quit",                         None),
    ]

    def __init__(self):
        self.screen: Screen = Screen.MAIN
        self.width:  int = 80
        self.height: int = 24

        # main menu
        self.menu_cursor: int = 0

        # login screen
        self.login_email: str = ""
        self.login_password: str = ""
        self.login_field: int = 0          # 0 = email, 1 = password
        self.login_status: str = ""
        self.login_running: bool = False

        # cookie screen
        self.cookie_input: str = ""
        self.cookie_saved: bool = os.path.isfile(COOKIES_FILE)
        self.cookie_status: str = ""
        self.cookie_retrieving: bool = False

        # add-book screen
        self.book_id_input: str = ""
        self.add_book_status: str = ""

        # queue
        self.queue: List[str] = []

        # export toggles
        self.export_markdown: bool = False
        self.export_db: bool = False
        self.export_rag: bool = False
        self.skip_if_downloaded: bool = False

        # download / calibre state
        self.books: dict[str, BookState] = {}   # book_id -> BookState
        self.dl_order: List[str] = []           # insertion order
        self.calibre_running: bool = False
        self.all_calibre_done: bool = False
        self.status_msg: str = ""
        self.dl_label: str = "Downloading"      # header for the download screen
        self.export_library_mode: bool = False  # True when running export-library
        self.dl_scroll: int = 0                 # scroll offset for download screen

        # settings screen
        from config import load_export_config
        _cfg = load_export_config()
        self.settings_fields: List[str] = [
            _cfg.markdown_dir,
            _cfg.rag_dir,
            _cfg.db_path,
            _cfg.folder_name_style,   # "title" or "id"
        ]
        self.settings_cursor: int = 0   # which field is focused
        self.settings_status: str = ""

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
        self.sync_error: str           = ""

        # program reference set after Program() creation
        self._program: Optional[tea.Program] = None

    # ── tea.Model interface ────────────────────────────────────────────────

    def init(self) -> Optional[tea.Cmd]:
        return tea.window_size()

    def update(self, msg: tea.Msg) -> Tuple["AppModel", Optional[tea.Cmd]]:
        if isinstance(msg, tea.WindowSizeMsg):
            self.width  = msg.width
            self.height = msg.height
            return self, None

        if isinstance(msg, tea.KeyMsg):
            return self._handle_key(msg.key)

        if isinstance(msg, tea.PasteMsg):
            if self.screen == Screen.LOGIN:
                if self.login_field == 0:
                    self.login_email = msg.text.strip()
                else:
                    self.login_password = msg.text.strip()
            elif self.screen == Screen.COOKIE:
                self.cookie_input = msg.text.strip()
            elif self.screen == Screen.ADD_BOOK:
                self.book_id_input = msg.text.strip()
            return self, None

        if isinstance(msg, ProgressMsg):
            self._on_progress(msg)
            return self, None

        if isinstance(msg, BookDoneMsg):
            self._on_book_done(msg)
            return self, None

        if isinstance(msg, BookErrorMsg):
            self._on_book_error(msg)
            return self, None

        if isinstance(msg, AllDownloadsDoneMsg):
            if not self.export_library_mode:
                self._start_calibre()
            else:
                self.all_calibre_done = True  # signal "all done" so footer updates
            return self, None

        if isinstance(msg, CalibreMsg):
            self._on_calibre(msg)
            return self, None

        if isinstance(msg, AllCalibreDoneMsg):
            self.all_calibre_done = True
            return self, None

        if isinstance(msg, LoginResultMsg):
            self.login_running = False
            if msg.cookies:
                with open(COOKIES_FILE, "w") as f:
                    json.dump(msg.cookies, f)
                self.cookie_saved = True
                self.login_status = "ok:Logged in and cookies saved."
            else:
                self.login_status = f"error:{msg.error or 'Login failed — check your credentials.'}"
            return self, None

        if isinstance(msg, BrowserCookieMsg):
            self.cookie_retrieving = False
            if msg.cookies:
                with open(COOKIES_FILE, "w") as f:
                    json.dump(msg.cookies, f)
                self.cookie_saved = True
                self.cookie_status = f"ok:Saved {len(msg.cookies)} cookies from browser."
            else:
                self.cookie_status = (
                    f"error:{msg.error or 'Browser extraction failed — try the CLI tool instead.'}"
                )
            return self, None

        if isinstance(msg, ClipboardMsg):
            if self.screen == Screen.COOKIE:
                if msg.text:
                    self.cookie_input = msg.text
                    self.cookie_status = f"ok:{len(msg.text)} chars read from clipboard — press Enter to save."
                else:
                    self.cookie_status = "error:Could not read clipboard. Install xclip, xsel, or wl-paste."
            elif self.screen == Screen.ADD_BOOK:
                if msg.text:
                    self.book_id_input = msg.text.strip()
                    self.add_book_status = ""
                else:
                    self.add_book_status = "error:Could not read clipboard. Install xclip, xsel, or wl-paste."
            return self, None

        return self, None

    # ── Key handling ───────────────────────────────────────────────────────

    def _handle_key(self, key: str) -> Tuple["AppModel", Optional[tea.Cmd]]:
        if key == "ctrl+c":
            return self, tea.quit_cmd

        dispatch = {
            Screen.MAIN:     self._key_main,
            Screen.LOGIN:    self._key_login,
            Screen.COOKIE:   self._key_cookie,
            Screen.ADD_BOOK: self._key_add_book,
            Screen.QUEUE:    self._key_queue,
            Screen.DOWNLOAD: self._key_download,
            Screen.CALIBRE:      self._key_calibre,
            Screen.SETTINGS:     self._key_settings,
            Screen.CALIBRE_SYNC: self._key_calibre_sync,
        }
        handler = dispatch.get(self.screen)
        if handler:
            return handler(key)
        return self, None

    def _key_main(self, key: str):
        n = len(self.MENU_ITEMS)
        if key in ("up", "k"):
            self.menu_cursor = (self.menu_cursor - 1) % n
        elif key in ("down", "j"):
            self.menu_cursor = (self.menu_cursor + 1) % n
        elif key in ("enter", " "):
            _, target = self.MENU_ITEMS[self.menu_cursor]
            if target is None:
                return self, tea.quit_cmd
            if target == "BROWSER":
                self.screen = Screen.COOKIE
                self.cookie_status = ""
                self._retrieve_from_browser()
            else:
                if target == Screen.CALIBRE_SYNC:
                    self._start_calibre_sync()
                    return self, None
                self.screen = target
                self.cookie_status = ""
                self.add_book_status = ""
        elif key == "q":
            return self, tea.quit_cmd
        return self, None

    def _key_login(self, key: str):
        if self.login_running:
            return self, None
        if key == "escape":
            self.screen = Screen.MAIN
            self.login_status = ""
        elif key in ("tab", "down", "enter") and self.login_field == 0:
            self.login_field = 1
        elif key in ("shift+tab", "up") and self.login_field == 1:
            self.login_field = 0
        elif key == "enter" and self.login_field == 1:
            self._do_login()
        elif key in ("backspace", "delete"):
            if self.login_field == 0:
                self.login_email = self.login_email[:-1]
            else:
                self.login_password = self.login_password[:-1]
            self.login_status = ""
        elif key == "ctrl+u":
            if self.login_field == 0:
                self.login_email = ""
            else:
                self.login_password = ""
            self.login_status = ""
        elif len(key) == 1 and key.isprintable():
            if self.login_field == 0:
                self.login_email += key
            else:
                self.login_password += key
            self.login_status = ""
        return self, None

    def _key_cookie(self, key: str):
        if key == "escape":
            self.screen = Screen.MAIN
        elif key == "enter":
            self._save_cookie()
        elif key in ("backspace", "delete"):
            self.cookie_input = self.cookie_input[:-1]
            self.cookie_status = ""
        elif key == "ctrl+u":
            self.cookie_input = ""
            self.cookie_status = ""
        elif key == "ctrl+v":
            self._read_clipboard()
        elif key in ("b", "B") and not self.cookie_retrieving:
            self._retrieve_from_browser()
        elif len(key) == 1:
            self.cookie_input += key
            self.cookie_status = ""
        return self, None

    def _key_add_book(self, key: str):
        if key == "escape":
            self.screen = Screen.MAIN
            self.book_id_input = ""
        elif key == "enter":
            self._add_book_to_queue()
        elif key == "backspace":
            self.book_id_input = self.book_id_input[:-1]
            self.add_book_status = ""
        elif key == "ctrl+u":
            self.book_id_input = ""
            self.add_book_status = ""
        elif key == "ctrl+v":
            self._read_clipboard_book()
        elif len(key) == 1 and key.isprintable():
            self.book_id_input += key
            self.add_book_status = ""
        return self, None

    def _key_queue(self, key: str):
        if key == "escape":
            self.screen = Screen.MAIN
        elif key in ("a", "A"):
            self.screen = Screen.ADD_BOOK
        elif key in ("r", "R"):
            self._start_downloads()
        elif key in ("s", "S"):
            self.screen = Screen.COOKIE
            self.cookie_status = ""
        elif key in ("c", "C"):
            # clear queue
            self.queue.clear()
        elif key in ("m", "M"):
            self.export_markdown = not self.export_markdown
        elif key in ("d", "D"):
            self.export_db = not self.export_db
        elif key in ("x", "X"):
            self.export_rag = not self.export_rag
        elif key in ("k", "K"):
            self.skip_if_downloaded = not self.skip_if_downloaded
        elif key in ("e", "E"):
            self._start_export_library()
        return self, None

    def _key_download(self, key: str):
        if self.all_calibre_done and key in ("q", "escape"):
            self.screen = Screen.MAIN
            return self, None
        total = len(self.dl_order)
        if key in ("up", "k"):
            self.dl_scroll = max(0, self.dl_scroll - 1)
        elif key in ("down", "j"):
            self.dl_scroll = min(max(0, total - 1), self.dl_scroll + 1)
        return self, None

    def _key_calibre(self, key: str):
        if self.all_calibre_done and key in ("q", "enter", "escape"):
            return self, tea.quit_cmd
        return self, None

    def _key_settings(self, key: str):
        n = len(self.settings_fields)
        if key == "escape":
            self.screen = Screen.MAIN
            self.settings_status = ""
        elif key in ("tab", "down"):
            self.settings_cursor = (self.settings_cursor + 1) % n
            self.settings_status = ""
        elif key in ("shift+tab", "up"):
            self.settings_cursor = (self.settings_cursor - 1) % n
            self.settings_status = ""
        elif key == "enter":
            self._save_settings()
        elif self.settings_cursor == 3:
            # Toggle field — space/enter/left/right cycle between "title" and "id"
            if key in ("enter", " ", "left", "right", "h", "l"):
                cur = self.settings_fields[3]
                self.settings_fields[3] = "id" if cur == "title" else "title"
                self.settings_status = ""
        elif key in ("backspace", "delete"):
            val = self.settings_fields[self.settings_cursor]
            self.settings_fields[self.settings_cursor] = val[:-1]
            self.settings_status = ""
        elif key == "ctrl+u":
            self.settings_fields[self.settings_cursor] = ""
            self.settings_status = ""
        elif len(key) == 1 and key.isprintable():
            self.settings_fields[self.settings_cursor] += key
            self.settings_status = ""
        return self, None

    def _save_settings(self):
        from config import ExportConfig, save_export_config
        cfg = ExportConfig(
            markdown_dir=self.settings_fields[0].strip(),
            rag_dir=self.settings_fields[1].strip(),
            db_path=self.settings_fields[2].strip(),
            folder_name_style=self.settings_fields[3],
        )
        try:
            save_export_config(cfg)
            self.settings_status = "ok:Settings saved to ~/.safaribooks.toml"
        except Exception as exc:
            self.settings_status = f"error:Save failed — {exc}"

    # ── Business logic ─────────────────────────────────────────────────────

    def _do_login(self):
        email = self.login_email.strip()
        password = self.login_password
        if not email:
            self.login_status = "error:Please enter your email."
            self.login_field = 0
            return
        if not password:
            self.login_status = "error:Please enter your password."
            return
        self.login_running = True
        self.login_status = ""

        def _worker():
            cookies = login_with_credentials(email, password)
            self._program.send(LoginResultMsg(
                cookies=cookies,
                error="" if cookies else "Login failed — check your credentials.",
            ))

        threading.Thread(target=_worker, daemon=True).start()

    def _save_cookie(self):
        raw = self.cookie_input.strip()
        if not raw:
            self.cookie_status = "error:No cookie value entered."
            return
        try:
            cookies = parse_cookie_string(raw)
        except json.JSONDecodeError:
            self.cookie_status = "error:Invalid JSON format."
            return
        except Exception as exc:
            self.cookie_status = f"error:Parse error: {exc}"
            return
        if not cookies:
            self.cookie_status = (
                "error:Could not parse cookies — paste the full Cookie header value."
            )
            return
        with open(COOKIES_FILE, "w") as f:
            json.dump(cookies, f)
        self.cookie_saved = True
        self.cookie_input = ""
        self.cookie_status = "ok:Saved."

    def _retrieve_from_browser(self):
        self.cookie_retrieving = True
        self.cookie_status = ""

        def _worker():
            cookies = get_oreilly_cookies_from_browser()
            self._program.send(BrowserCookieMsg(
                cookies=cookies,
                error="" if cookies else "No O'Reilly cookies found in browser.",
            ))

        threading.Thread(target=_worker, daemon=True).start()

    def _read_clipboard(self):
        """Read clipboard content via system tools and deliver as ClipboardMsg."""
        self.cookie_status = "ok:Reading clipboard…"

        def _worker():
            _cmds = [
                ["wl-paste", "--no-newline"],
                ["xclip", "-selection", "clipboard", "-o"],
                ["xsel", "--clipboard", "--output"],
            ]
            for cmd in _cmds:
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                    if result.returncode == 0 and result.stdout.strip():
                        self._program.send(ClipboardMsg(result.stdout.strip()))
                        return
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    continue
            self._program.send(ClipboardMsg(""))

        threading.Thread(target=_worker, daemon=True).start()

    def _read_clipboard_book(self):
        """Read clipboard content and deliver as ClipboardMsg for the add-book screen."""
        def _worker():
            _cmds = [
                ["wl-paste", "--no-newline"],
                ["xclip", "-selection", "clipboard", "-o"],
                ["xsel", "--clipboard", "--output"],
            ]
            for cmd in _cmds:
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                    if result.returncode == 0 and result.stdout.strip():
                        self._program.send(ClipboardMsg(result.stdout.strip()))
                        return
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    continue
            self._program.send(ClipboardMsg(""))

        threading.Thread(target=_worker, daemon=True).start()

    def _cookie_age_mins(self) -> int:
        """Minutes since cookies.json was last written, or -1 if file is missing."""
        if not os.path.isfile(COOKIES_FILE):
            return -1
        return int((time.time() - os.path.getmtime(COOKIES_FILE)) / 60)

    def _cookie_age_str(self) -> str:
        mins = self._cookie_age_mins()
        if mins < 0:
            return ""
        if mins < 1:
            return "< 1 min ago"
        if mins == 1:
            return "1 min ago"
        return f"{mins} min ago"

    def _add_book_to_queue(self):
        book_id = self.book_id_input.strip()
        if not book_id:
            self.add_book_status = "error:Please enter a book ID."
            return
        if not book_id.isdigit():
            self.add_book_status = "error:Book ID must be numeric."
            return
        if book_id in self.queue:
            self.add_book_status = "error:Book already in queue."
            return
        self.queue.append(book_id)
        self.book_id_input = ""
        self.add_book_status = f"ok:Added {book_id} to queue."

    def _start_downloads(self):
        if not self.queue:
            return
        if not os.path.isfile(COOKIES_FILE):
            self.status_msg = "No cookies.json found — press 's' to set your cookie first."
            return

        # Initialise state entries
        self.dl_order = list(self.queue)
        for book_id in self.dl_order:
            self.books[book_id] = BookState(book_id=book_id)
        self.queue.clear()

        self.dl_label = "Downloading"
        self.export_library_mode = False
        self.dl_scroll = 0
        self.screen = Screen.DOWNLOAD
        worker = DownloadWorker(
            self.dl_order, self._program,
            export_markdown=self.export_markdown,
            export_db=self.export_db,
            export_rag=self.export_rag,
            skip_if_downloaded=self.skip_if_downloaded,
        )
        worker.start()

    def _start_export_library(self):
        if not (self.export_markdown or self.export_db or self.export_rag):
            self.status_msg = "Select at least one export format (m/d/x) before running."
            return

        import re as _re
        books_dir = os.path.join(PATH, "Books")
        if not os.path.isdir(books_dir):
            self.status_msg = "Books/ directory not found."
            return

        # Quick scan — just directory names, no file I/O
        _dir_re = _re.compile(r'^.+\((\w+)\)$')
        book_ids = []
        for entry in sorted(os.scandir(books_dir), key=lambda e: e.name):
            if entry.is_dir() and _dir_re.match(entry.name):
                m = _dir_re.match(entry.name)
                book_ids.append(m.group(1))

        if not book_ids:
            self.status_msg = "No downloaded books found in Books/."
            return

        self.dl_order = book_ids
        self.books = {bid: BookState(book_id=bid) for bid in book_ids}
        self.all_calibre_done = False
        self.calibre_running = False
        self.dl_label = "Exporting Library"
        self.export_library_mode = True
        self.dl_scroll = 0
        self.status_msg = ""
        self.screen = Screen.DOWNLOAD

        worker = ExportLibraryWorker(
            books_dir=books_dir,
            book_ids=book_ids,
            program=self._program,
            export_markdown=self.export_markdown,
            export_db=self.export_db,
            export_rag=self.export_rag,
        )
        worker.start()

    def _start_calibre(self):
        successful = [self.books[bid] for bid in self.dl_order if not self.books[bid].failed]
        if not successful:
            self.all_calibre_done = True
            return
        self.calibre_running = True
        self.screen = Screen.CALIBRE
        CalibreWorker(successful, self._program).start()

    # ── Progress callbacks (called from worker threads via program.send) ───

    def _on_progress(self, msg: ProgressMsg):
        if msg.book_id not in self.books:
            self.books[msg.book_id] = BookState(book_id=msg.book_id)
        b = self.books[msg.book_id]
        b.stage = msg.stage
        if msg.percent >= 0:
            b.percent = msg.percent

    def _on_book_done(self, msg: BookDoneMsg):
        if msg.book_id not in self.books:
            self.books[msg.book_id] = BookState(book_id=msg.book_id)
        b = self.books[msg.book_id]
        b.title     = msg.title
        b.epub_path = msg.epub_path
        b.done      = True
        b.percent   = 1.0
        b.stage     = "Download complete"

    def _on_book_error(self, msg: BookErrorMsg):
        if msg.book_id not in self.books:
            self.books[msg.book_id] = BookState(book_id=msg.book_id)
        b = self.books[msg.book_id]
        b.error  = msg.error
        b.failed = True
        b.stage  = "Failed"

    def _on_calibre(self, msg: CalibreMsg):
        if msg.book_id not in self.books:
            return
        b = self.books[msg.book_id]
        if msg.stage == "converting":
            b.stage = "Converting with Calibre…"
        elif msg.stage == "done":
            b.calibre_path = msg.message
            b.calibre_done = True
            b.stage = "Calibre done"
        elif msg.stage == "error":
            b.calibre_failed = True
            b.error = msg.message
            b.stage = "Calibre failed"

    # ── View ───────────────────────────────────────────────────────────────

    def view(self) -> str:
        views = {
            Screen.MAIN:     self._view_main,
            Screen.LOGIN:    self._view_login,
            Screen.COOKIE:   self._view_cookie,
            Screen.ADD_BOOK: self._view_add_book,
            Screen.QUEUE:    self._view_queue,
            Screen.DOWNLOAD: self._view_download,
            Screen.CALIBRE:      self._view_calibre,
            Screen.SETTINGS:     self._view_settings,
            Screen.CALIBRE_SYNC: self._view_calibre_sync,
        }
        render = views.get(self.screen, self._view_main)
        return render() + "\n"

    def _header(self, subtitle: str = "") -> str:
        title = title_style.render("  KeroOle  ")
        if subtitle:
            sub = Style().foreground(C_MUTED).render(f"  {subtitle}")
            return join_horizontal(Top, title, sub)
        return title

    def _footer(self, hints: str) -> str:
        return hint_style.render(hints)

    def _library_book_count(self) -> int:
        """Count book directories in Books/ — fast directory scan, no file reads."""
        import re as _re
        books_dir = os.path.join(PATH, "Books")
        if not os.path.isdir(books_dir):
            return 0
        _dir_re = _re.compile(r'^.+\((\w+)\)$')
        return sum(
            1 for e in os.scandir(books_dir)
            if e.is_dir() and _dir_re.match(e.name)
        )

    # Main menu ───────────────────────────────────────────────────────────────

    def _view_main(self) -> str:
        lines = [self._header(), ""]

        # Cookie status badge — always read from disk so external saves are reflected
        cookie_exists = os.path.isfile(COOKIES_FILE)
        if cookie_exists:
            age_mins = self._cookie_age_mins()
            age_str  = self._cookie_age_str()
            if age_mins > 15:
                badge = Style().foreground(C_YELLOW).bold(True).render(
                    f"● Cookie: saved ({age_str}) ⚠ may be expired"
                )
            else:
                badge = success_style.render(f"● Cookie: saved ({age_str})")
        else:
            badge = error_style.render("○ Cookie: not set")
        lines.append("  " + badge)
        lines.append("")

        # Menu items
        lib_count = self._library_book_count()
        q = len(self.queue)
        for i, (label, _) in enumerate(self.MENU_ITEMS):
            is_queue = label == "View / Run Queue"
            if is_queue:
                label = "View / Run Queue / Export"
            if i == self.menu_cursor:
                lines.append(cursor_style.render(f"  ▶ {label}"))
            else:
                lines.append(f"    {label}")
            if is_queue:
                lib_str = f"{lib_count} in library" if lib_count else "library empty"
                lines.append(hint_style.render(f"      {q} queued · {lib_str}"))

        lines.append("")
        lines.append(self._footer("↑/↓  move    Enter  select    q  quit"))

        content = "\n".join(lines)
        return panel_style.width(min(self.width - 4, 60)).render(content)

    # Login screen ────────────────────────────────────────────────────────────

    def _view_login(self) -> str:
        lines = [self._header("Login with Email/Password"), ""]

        # Current cookie status
        if os.path.isfile(COOKIES_FILE):
            age_str = self._cookie_age_str()
            lines.append(success_style.render(f"● Already logged in ({age_str}) — log in again to refresh"))
        else:
            lines.append(hint_style.render("○ No session saved — enter credentials below"))
        lines.append("")

        box_w = min(self.width - 12, 48)

        def _field(label: str, value: str, focused: bool, masked: bool = False) -> str:
            display = ("*" * len(value)) if masked else value
            cursor  = "█" if focused else ""
            border_color = C_ACCENT if focused else C_MUTED
            box = (
                Style()
                .border(normal_border())
                .border_foreground(border_color)
                .padding(0, 1)
                .width(box_w)
                .render(display + cursor)
            )
            lbl = (accent_style if focused else label_style).render(label)
            return lbl + "\n" + box

        lines.append(_field("Email", self.login_email, self.login_field == 0))
        lines.append("")
        lines.append(_field("Password", self.login_password, self.login_field == 1, masked=True))
        lines.append("")

        if self.login_running:
            lines.append(Style().foreground(C_YELLOW).render("  ⟳  Logging in…"))
        elif self.login_status:
            kind, _, msg = self.login_status.partition(":")
            if kind == "ok":
                lines.append(success_style.render("✓ " + msg))
            else:
                lines.append(error_style.render("✗ " + msg))
        lines.append("")

        lines.append(self._footer("Tab/↓  next field    Enter  submit    Ctrl+U  clear    Esc  back"))
        content = "\n".join(lines)
        return panel_style.width(min(self.width - 4, 60)).render(content)

    # Cookie screen ───────────────────────────────────────────────────────────

    def _view_cookie(self) -> str:
        lines = [self._header("Set Session Cookie"), ""]

        # Current file status — reflects CLI saves too
        cookie_exists = os.path.isfile(COOKIES_FILE)
        if cookie_exists:
            age_str = self._cookie_age_str()
            age_mins = self._cookie_age_mins()
            if age_mins > 15:
                lines.append(Style().foreground(C_YELLOW).bold(True).render(
                    f"● cookies.json saved ({age_str}) ⚠ may be expired — update below"
                ))
            else:
                lines.append(success_style.render(
                    f"● cookies.json saved ({age_str}) ✓ — ready, or update below"
                ))
        else:
            lines.append(error_style.render("○ cookies.json not found — set a cookie below"))
        lines.append("")

        # Option 1: browser auto-retrieve
        lines.append(accent_style.render("Option 1 — Auto-retrieve from browser  [press b]"))
        if self.cookie_retrieving:
            lines.append(Style().foreground(C_YELLOW).render("  ⟳  Retrieving cookies from browser…"))
        else:
            lines.append(label_style.render("  Reads Chrome/Firefox cookies directly from disk."))
            lines.append(hint_style.render("  May fail over SSH or if Chrome is still running — use Option 2 then."))
        lines.append("")

        # Option 2: paste from DevTools
        lines.append(accent_style.render("Option 2 — Paste from DevTools  [Ctrl+V or Enter to save]"))
        lines.append(label_style.render("  1. DevTools (F12) → Network → any learning.oreilly.com request"))
        lines.append(label_style.render("  2. Headers → Request Headers → right-click Cookie → Copy value"))
        lines.append(label_style.render("  3. Press Ctrl+V to read from clipboard, then Enter to save"))
        lines.append(hint_style.render("  Tip: if Ctrl+V fails, use the CLI (most reliable for long cookies):"))
        lines.append(hint_style.render("       xclip -o | python3 retrieve_cookies.py --stdin"))
        lines.append("")

        # Input display — show last 60 chars + character count
        char_count = len(self.cookie_input)
        truncated = self.cookie_input[-60:] if char_count > 60 else self.cookie_input
        count_str = hint_style.render(f"  ({char_count} chars captured)")
        input_box = (
            Style()
            .border(normal_border())
            .border_foreground(C_ACCENT)
            .padding(0, 1)
            .width(min(self.width - 12, 64))
            .render(truncated + "█" if self.cookie_input else "█")
        )
        lines.append(input_box)
        if char_count > 0:
            lines.append(count_str)
        lines.append("")

        # Status
        if self.cookie_status:
            kind, _, msg = self.cookie_status.partition(":")
            if kind == "ok":
                lines.append(success_style.render("✓ " + msg))
            else:
                lines.append(error_style.render("✗ " + msg))
            lines.append("")

        lines.append(self._footer("Enter  save    Ctrl+V  paste    b  browser    Backspace  clear    Esc  back"))
        content = "\n".join(lines)
        return panel_style.width(min(self.width - 4, 72)).render(content)

    # Add-book screen ─────────────────────────────────────────────────────────

    def _view_add_book(self) -> str:
        lines = [self._header("Add Book to Queue"), ""]

        lines.append(label_style.render("Enter the numeric Book ID from the O'Reilly URL:"))
        lines.append(label_style.render("  learning.oreilly.com/library/view/title/XXXXXXXXXXX/"))
        lines.append("")

        cursor = "█"
        input_box = (
            Style()
            .border(normal_border())
            .border_foreground(C_ACCENT)
            .padding(0, 1)
            .width(32)
            .render(self.book_id_input + cursor)
        )
        lines.append(input_box)
        lines.append("")

        if self.add_book_status:
            kind, _, msg = self.add_book_status.partition(":")
            if kind == "ok":
                lines.append(success_style.render("✓ " + msg))
            else:
                lines.append(error_style.render("✗ " + msg))
            lines.append("")

        lines.append(self._footer("Enter  add    Ctrl+V  paste    Esc  back"))
        content = "\n".join(lines)
        return panel_style.width(min(self.width - 4, 60)).render(content)

    # Queue screen ────────────────────────────────────────────────────────────

    def _view_queue(self) -> str:
        lines = [self._header("Queue / Export"), ""]

        if not self.queue:
            lines.append(hint_style.render("  Download queue is empty — press 'a' to add books, or 'e' to export existing downloads."))
        else:
            for i, book_id in enumerate(self.queue, 1):
                lines.append(f"  {i}. {book_id}")

        lines.append("")

        cookie_exists = os.path.isfile(COOKIES_FILE)
        if not cookie_exists:
            lines.append(error_style.render("  ⚠  Cookie not set — press 's' to set one before running."))
        else:
            age_mins = self._cookie_age_mins()
            age_str  = self._cookie_age_str()
            if age_mins > 15:
                lines.append(Style().foreground(C_YELLOW).bold(True).render(
                    f"  ⚠  Cookie saved {age_str} — may be expired.  Press 's' to refresh."
                ))
            else:
                lines.append(success_style.render(f"  ● Cookie ready  ({age_str})"))
        lines.append("")

        if self.status_msg:
            lines.append(error_style.render("  " + self.status_msg))
            lines.append("")

        # Export toggles
        def _toggle(label: str, value: bool) -> str:
            marker = success_style.render("✓") if value else hint_style.render("○")
            return f"  {marker} {label}"

        lines.append(_toggle("[m] Markdown export", self.export_markdown))
        lines.append(_toggle("[d] Content DB",      self.export_db))
        lines.append(_toggle("[x] RAG JSONL",       self.export_rag))
        lines.append(_toggle("[k] Skip if downloaded", self.skip_if_downloaded))
        lines.append("")

        lines.append(self._footer(
            "a  add    r  run    e  export library    s  set cookie    c  clear    m/d/x/k  toggles    Esc  back"
        ))
        content = "\n".join(lines)
        return panel_style.width(min(self.width - 4, 60)).render(content)

    # Download screen ─────────────────────────────────────────────────────────

    def _view_download(self) -> str:
        total = len(self.dl_order)
        # Each book renders as 3 lines (id, status, blank). Reserve 4 for
        # header + footer + scroll indicator.
        rows_available = max(3, self.height - 4)
        per_book = 3
        visible_count = max(1, rows_available // per_book)

        # Auto-scroll: keep the first in-progress book visible
        active_idx = None
        for i, bid in enumerate(self.dl_order):
            b = self.books.get(bid)
            if b and not b.done and not b.failed:
                active_idx = i
                break
        if active_idx is not None:
            # Clamp scroll so active book is in window
            if active_idx < self.dl_scroll:
                self.dl_scroll = active_idx
            elif active_idx >= self.dl_scroll + visible_count:
                self.dl_scroll = active_idx - visible_count + 1

        self.dl_scroll = max(0, min(self.dl_scroll, max(0, total - visible_count)))
        visible_ids = self.dl_order[self.dl_scroll: self.dl_scroll + visible_count]

        lines = [self._header(self.dl_label), ""]

        for book_id in visible_ids:
            b = self.books.get(book_id)
            if b is None:
                continue

            if b.title:
                id_line = f"{book_id}  {value_style.render(b.title[:38])}"
            else:
                id_line = book_id

            if b.failed:
                status = error_style.render("✗ " + (b.error[:50] if b.error else "Failed"))
                lines.append(f"  {id_line}")
                lines.append(f"  {status}")
            elif b.done:
                status = success_style.render("✓ Complete")
                lines.append(f"  {id_line}")
                lines.append(f"  {status}")
            else:
                lines.append(f"  {id_line}")
                lines.append(f"  {render_bar(b.percent)}  {hint_style.render(b.stage[:38])}")
            lines.append("")

        # Scroll indicator when list is longer than the viewport
        if total > visible_count:
            end = min(self.dl_scroll + visible_count, total)
            scroll_hint = hint_style.render(
                f"  Showing {self.dl_scroll + 1}–{end} of {total}   ↑/↓ to scroll"
            )
            lines.append(scroll_hint)
            lines.append("")

        if self.all_calibre_done:
            lines.append(self._footer("↑/↓  scroll    Esc  back to menu    q  quit"))
        else:
            lines.append(self._footer("↑/↓  scroll    Ctrl+C  cancel"))
        content = "\n".join(lines)
        return panel_style.width(min(self.width - 4, 72)).render(content)

    # Calibre screen ──────────────────────────────────────────────────────────

    def _view_calibre(self) -> str:
        lines = [self._header("Calibre Conversion"), ""]

        for book_id in self.dl_order:
            b = self.books.get(book_id)
            if b is None or b.failed:
                continue

            label = b.title[:40] if b.title else book_id

            if b.calibre_failed:
                status = error_style.render(f"✗ {b.error[:50]}")
            elif b.calibre_done:
                status = success_style.render(f"✓ {b.calibre_path}")
            else:
                status = Style().foreground(C_YELLOW).render("⟳ Converting…")

            lines.append(f"  {label}")
            lines.append(f"  {status}")
            lines.append("")

        if self.all_calibre_done:
            lines.append(success_style.render("  All done!"))
            lines.append("")
            lines.append(self._footer("Enter/q  quit"))
        else:
            lines.append(self._footer("Ctrl+C  cancel"))

        content = "\n".join(lines)
        return panel_style.width(min(self.width - 4, 72)).render(content)

    # Settings screen ─────────────────────────────────────────────────────────

    def _view_settings(self) -> str:
        lines = [self._header("Export Paths / Settings"), ""]

        box_w = min(self.width - 12, 56)

        # (label, placeholder when empty, sub-hint shown below field, field index)
        field_defs = [
            ("Markdown output dir",
             "blank = next to each book in Books/",
             "e.g. ~/Documents/Books/MD  — one subfolder per book is created inside",
             0),
            ("RAG JSONL output dir",
             "blank = next to each book in Books/",
             "e.g. ~/Documents/Books/RAG  — all JSONL files land flat inside",
             1),
            ("Library DB path",
             "blank = Books/library.db",
             "e.g. ~/Documents/Books/library.db  — full path to the SQLite file",
             2),
        ]

        lines.append(hint_style.render(
            "  Leave blank to keep the default path inside each book's folder."
        ))
        lines.append(hint_style.render(
            "  Paths support ~ (e.g. ~/Documents/Books).  Tab/↓ to move.  Enter to save."
        ))
        lines.append("")

        for label, placeholder, sub_hint, idx in field_defs:
            focused = self.settings_cursor == idx
            val = self.settings_fields[idx]
            cursor  = "█" if focused else ""
            border_color = C_ACCENT if focused else C_MUTED
            box = (
                Style()
                .border(normal_border())
                .border_foreground(border_color)
                .padding(0, 1)
                .width(box_w)
                .render((val + cursor) if val else (hint_style.render(placeholder) + cursor))
            )
            lbl = (accent_style if focused else label_style).render(label)
            lines.append(lbl)
            lines.append(box)
            lines.append(hint_style.render(f"  ↳ {sub_hint}"))
            lines.append("")

        # Toggle: folder name style
        focused = self.settings_cursor == 3
        style_val = self.settings_fields[3] or "title"
        title_sel = accent_style.render("[ Title ]") if style_val == "title" else hint_style.render("[ Title ]")
        id_sel    = accent_style.render("[ ID ]")    if style_val == "id"    else hint_style.render("[ ID ]")
        lbl = (accent_style if focused else label_style).render("Folder name style")
        toggle_row = f"  {title_sel}  {id_sel}"
        if focused:
            toggle_row += "  " + hint_style.render("← → or Space to switch")
        lines.append(lbl)
        lines.append(toggle_row)
        lines.append(hint_style.render("  ↳ How export subfolders are named — by book title or numeric book ID"))
        lines.append("")

        if self.settings_status:
            kind, _, msg = self.settings_status.partition(":")
            if kind == "ok":
                lines.append(success_style.render("✓ " + msg))
            else:
                lines.append(error_style.render("✗ " + msg))
            lines.append("")

        lines.append(self._footer("Tab/↓  next field    Enter  save    Ctrl+U  clear    Space  toggle    Esc  back"))
        content = "\n".join(lines)
        return panel_style.width(min(self.width - 4, 68)).render(content)

    # ── Calibre sync screen ────────────────────────────────────────────────

    def _start_calibre_sync(self):
        self.sync_scanning    = True
        self.sync_entries     = []
        self.sync_selected    = set()
        self.sync_cursor      = 0
        self.sync_scroll      = 0
        self.sync_adding      = False
        self.sync_add_status  = {}
        self.sync_all_done    = False
        self.sync_error       = ""
        self.screen           = Screen.CALIBRE_SYNC

    def _key_calibre_sync(self, key: str):
        if key == "escape":
            self.screen = Screen.MAIN
        return self, None

    def _view_calibre_sync(self) -> str:
        lines = [self._header("Sync with Calibre Library"), ""]
        lines.append("  Scanning…" if self.sync_scanning else "  (not yet implemented)")
        lines.append("")
        lines.append(self._footer("Esc  back"))
        content = "\n".join(lines)
        return panel_style.width(min(self.width - 4, 72)).render(content)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    model = AppModel()
    program = tea.Program(model, alt_screen=True)
    model._program = program  # back-reference so workers can send messages
    try:
        final = program.run()
    except (tea.ErrInterrupted, KeyboardInterrupt):
        pass


if __name__ == "__main__":
    main()
