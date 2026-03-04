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

from safaribooks import COOKIES_FILE, SafariBooks, SafariBooksError

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
    COOKIE     = auto()
    ADD_BOOK   = auto()
    QUEUE      = auto()
    DOWNLOAD   = auto()
    CALIBRE    = auto()


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

    def __init__(self, book_ids: List[str], program: tea.Program, kindle: bool = False):
        self.book_ids = book_ids
        self.program = program
        self.kindle = kindle
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
                        "--pretty-print-html",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=600,
                )
                if result.returncode == 0:
                    self.program.send(CalibreMsg(book.book_id, "done", out_path))
                else:
                    self.program.send(CalibreMsg(book.book_id, "error", result.stderr.strip()[:200]))
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
        ("Set Session Cookie",   Screen.COOKIE),
        ("Add Book to Queue",    Screen.ADD_BOOK),
        ("View / Run Queue",     Screen.QUEUE),
        ("Quit",                 None),
    ]

    def __init__(self):
        self.screen: Screen = Screen.MAIN
        self.width:  int = 80
        self.height: int = 24

        # main menu
        self.menu_cursor: int = 0

        # cookie screen
        self.cookie_input: str = ""
        self.cookie_saved: bool = os.path.isfile(COOKIES_FILE)
        self.cookie_status: str = ""

        # add-book screen
        self.book_id_input: str = ""
        self.add_book_status: str = ""

        # queue
        self.queue: List[str] = []

        # download / calibre state
        self.books: dict[str, BookState] = {}   # book_id -> BookState
        self.dl_order: List[str] = []           # insertion order
        self.calibre_running: bool = False
        self.all_calibre_done: bool = False
        self.status_msg: str = ""

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
            if self.screen == Screen.COOKIE:
                self.cookie_input = msg.text.strip()
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
            self._start_calibre()
            return self, None

        if isinstance(msg, CalibreMsg):
            self._on_calibre(msg)
            return self, None

        if isinstance(msg, AllCalibreDoneMsg):
            self.all_calibre_done = True
            return self, None

        return self, None

    # ── Key handling ───────────────────────────────────────────────────────

    def _handle_key(self, key: str) -> Tuple["AppModel", Optional[tea.Cmd]]:
        if key == "ctrl+c":
            return self, tea.quit_cmd

        dispatch = {
            Screen.MAIN:     self._key_main,
            Screen.COOKIE:   self._key_cookie,
            Screen.ADD_BOOK: self._key_add_book,
            Screen.QUEUE:    self._key_queue,
            Screen.DOWNLOAD: self._key_download,
            Screen.CALIBRE:  self._key_calibre,
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
            self.screen = target
            self.cookie_status = ""
            self.add_book_status = ""
        elif key == "q":
            return self, tea.quit_cmd
        return self, None

    def _key_cookie(self, key: str):
        if key == "escape":
            self.screen = Screen.MAIN
        elif key == "enter":
            self._save_cookie()
        elif key == "backspace":
            self.cookie_input = self.cookie_input[:-1]
            self.cookie_status = ""
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
        elif key in ("c", "C"):
            # clear queue
            self.queue.clear()
        return self, None

    def _key_download(self, key: str):
        # nothing interactive during download
        return self, None

    def _key_calibre(self, key: str):
        if self.all_calibre_done and key in ("q", "enter", "escape"):
            return self, tea.quit_cmd
        return self, None

    # ── Business logic ─────────────────────────────────────────────────────

    def _save_cookie(self):
        raw = self.cookie_input.strip()
        if not raw:
            self.cookie_status = "error:No cookie value entered."
            return

        # Accept either a raw cookie string or a JSON dict
        if raw.startswith("{"):
            try:
                cookies = json.loads(raw)
            except json.JSONDecodeError:
                self.cookie_status = "error:Invalid JSON cookie format."
                return
        else:
            # Parse key=value; key=value; ... style
            cookies = {}
            for pair in raw.split(";"):
                pair = pair.strip()
                if "=" in pair:
                    k, _, v = pair.partition("=")
                    cookies[k.strip()] = v.strip()

        if not cookies:
            self.cookie_status = "error:Could not parse any cookies."
            return

        with open(COOKIES_FILE, "w") as f:
            json.dump(cookies, f)
        self.cookie_saved = True
        self.cookie_input = ""
        self.cookie_status = "ok:Cookie saved successfully."

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
            self.status_msg = "No cookies.json found — set your cookie first."
            return

        # Initialise state entries
        self.dl_order = list(self.queue)
        for book_id in self.dl_order:
            self.books[book_id] = BookState(book_id=book_id)
        self.queue.clear()

        self.screen = Screen.DOWNLOAD
        worker = DownloadWorker(self.dl_order, self._program)
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
            Screen.COOKIE:   self._view_cookie,
            Screen.ADD_BOOK: self._view_add_book,
            Screen.QUEUE:    self._view_queue,
            Screen.DOWNLOAD: self._view_download,
            Screen.CALIBRE:  self._view_calibre,
        }
        render = views.get(self.screen, self._view_main)
        return render() + "\n"

    def _header(self, subtitle: str = "") -> str:
        title = title_style.render("  SafariBooks  ")
        if subtitle:
            sub = Style().foreground(C_MUTED).render(f"  {subtitle}")
            return join_horizontal(Top, title, sub)
        return title

    def _footer(self, hints: str) -> str:
        return hint_style.render(hints)

    # Main menu ───────────────────────────────────────────────────────────────

    def _view_main(self) -> str:
        lines = [self._header(), ""]

        # Cookie status badge
        if self.cookie_saved:
            badge = success_style.render("● Cookie: saved")
        else:
            badge = error_style.render("○ Cookie: not set")
        lines.append("  " + badge)
        lines.append("")

        # Menu items
        for i, (label, _) in enumerate(self.MENU_ITEMS):
            # Append queue count to the queue item
            if label == "View / Run Queue":
                label = f"View / Run Queue  ({len(self.queue)} book{'s' if len(self.queue) != 1 else ''})"
            if i == self.menu_cursor:
                lines.append(cursor_style.render(f"  ▶ {label}"))
            else:
                lines.append(f"    {label}")

        lines.append("")
        lines.append(self._footer("↑/↓  move    Enter  select    q  quit"))

        content = "\n".join(lines)
        return panel_style.width(min(self.width - 4, 60)).render(content)

    # Cookie screen ───────────────────────────────────────────────────────────

    def _view_cookie(self) -> str:
        lines = [self._header("Set Session Cookie"), ""]

        lines.append(label_style.render("Paste your O'Reilly browser session cookie below."))
        lines.append(label_style.render("Tip: use Ctrl+Shift+V or terminal paste (bracketed paste supported)."))
        lines.append("")

        # Input display
        display = self.cookie_input if self.cookie_input else ""
        truncated = display[-60:] if len(display) > 60 else display
        input_box = (
            Style()
            .border(normal_border())
            .border_foreground(C_ACCENT)
            .padding(0, 1)
            .width(min(self.width - 12, 64))
            .render(truncated + "█" if self.cookie_input else "█")
        )
        lines.append(input_box)
        lines.append("")

        # Status
        if self.cookie_status:
            kind, _, msg = self.cookie_status.partition(":")
            if kind == "ok":
                lines.append(success_style.render("✓ " + msg))
            else:
                lines.append(error_style.render("✗ " + msg))
            lines.append("")

        lines.append(self._footer("Enter  save    Esc  back    Ctrl+C  quit"))
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

        lines.append(self._footer("Enter  add    Esc  back"))
        content = "\n".join(lines)
        return panel_style.width(min(self.width - 4, 60)).render(content)

    # Queue screen ────────────────────────────────────────────────────────────

    def _view_queue(self) -> str:
        lines = [self._header("Download Queue"), ""]

        if not self.queue:
            lines.append(hint_style.render("  Queue is empty."))
        else:
            for i, book_id in enumerate(self.queue, 1):
                lines.append(f"  {i}. {book_id}")

        lines.append("")

        if not self.cookie_saved:
            lines.append(error_style.render("  ⚠  Cookie not set — run will fail."))
            lines.append("")

        if self.status_msg:
            lines.append(error_style.render("  " + self.status_msg))
            lines.append("")

        lines.append(self._footer("a  add book    r  run queue    c  clear    Esc  back"))
        content = "\n".join(lines)
        return panel_style.width(min(self.width - 4, 60)).render(content)

    # Download screen ─────────────────────────────────────────────────────────

    def _view_download(self) -> str:
        lines = [self._header("Downloading"), ""]

        for book_id in self.dl_order:
            b = self.books.get(book_id)
            if b is None:
                continue

            if b.title:
                id_line = f"{book_id}  {value_style.render(b.title[:40])}"
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
                lines.append(f"  {render_bar(b.percent)}  {hint_style.render(b.stage[:40])}")
            lines.append("")

        lines.append(self._footer("Ctrl+C  cancel"))
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
