"""
config.py — User-level configuration for SafariBooks / KeroOle.

Config file: ~/.safaribooks.toml

Example:
    [exports]
    markdown_dir      = "~/Documents/Books/markdown"
    rag_dir           = "~/Documents/Books/rag"
    db_path           = "~/Documents/Books/library.db"
    folder_name_style = "title"   # "title" or "id"

All paths support ~ expansion.  If a key is absent, the default behaviour
(paths relative to each book's directory inside Books/) is used.
"""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path.home() / ".safaribooks.toml"

FOLDER_NAME_STYLES = ("title", "id")


@dataclass
class ExportConfig:
    """Resolved export-path configuration."""
    # If empty string, fall back to the per-book default inside Books/
    markdown_dir: str      = ""
    rag_dir: str           = ""
    db_path: str           = ""
    # "title" → sanitized book title;  "id" → numeric book ID
    folder_name_style: str = "title"

    def resolved_markdown_dir(self) -> str:
        """Absolute path for the markdown output base dir, or '' for default."""
        return str(Path(self.markdown_dir).expanduser()) if self.markdown_dir else ""

    def resolved_rag_dir(self) -> str:
        """Absolute path for the RAG JSONL output dir, or '' for default."""
        return str(Path(self.rag_dir).expanduser()) if self.rag_dir else ""

    def resolved_db_path(self) -> str:
        """Absolute path for library.db, or '' for default."""
        return str(Path(self.db_path).expanduser()) if self.db_path else ""


# ---------------------------------------------------------------------------
# Folder-name helpers
# ---------------------------------------------------------------------------

def sanitize_folder_name(name: str, max_len: int = 80) -> str:
    """Return a filesystem-safe version of name.

    Replaces characters invalid on Windows/Linux/macOS, collapses whitespace,
    strips leading/trailing dots, and truncates to max_len.
    """
    name = re.sub(r'[/\\:*?"<>|]', '-', name)   # forbidden chars → dash
    name = re.sub(r'\s+', ' ', name).strip()      # collapse whitespace
    name = name.strip('.')                         # no leading/trailing dots
    return name[:max_len] if name else "unknown"


def book_folder_name(book_info: dict, book_id: str, style: str = "title") -> str:
    """Return the folder/filename stem to use for a book's export output.

    style="title" → sanitized book title (falls back to book_id if title missing)
    style="id"    → numeric book_id as-is
    """
    if style == "id":
        return book_id
    title = (book_info.get("title") or "").strip()
    if not title:
        return book_id
    return sanitize_folder_name(title)


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------

def _load_toml() -> dict:
    """Load ~/.safaribooks.toml, returning empty dict if missing or unreadable."""
    if not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError:
        return {}


def load_export_config() -> ExportConfig:
    """Load [exports] section from ~/.safaribooks.toml."""
    data = _load_toml()
    e = data.get("exports", {})
    raw_style = e.get("folder_name_style", "title")
    return ExportConfig(
        markdown_dir=e.get("markdown_dir", ""),
        rag_dir=e.get("rag_dir", ""),
        db_path=e.get("db_path", ""),
        folder_name_style=raw_style if raw_style in FOLDER_NAME_STYLES else "title",
    )


def save_export_config(cfg: ExportConfig) -> None:
    """Write [exports] section back to ~/.safaribooks.toml.

    Preserves any other sections already present in the file.
    """
    existing = _load_toml()
    existing["exports"] = {
        "markdown_dir":      cfg.markdown_dir,
        "rag_dir":           cfg.rag_dir,
        "db_path":           cfg.db_path,
        "folder_name_style": cfg.folder_name_style
                             if cfg.folder_name_style in FOLDER_NAME_STYLES
                             else "title",
    }

    lines = []
    for section, values in existing.items():
        lines.append(f"[{section}]")
        for key, val in values.items():
            escaped = val.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{key} = "{escaped}"')
        lines.append("")

    CONFIG_PATH.write_text("\n".join(lines), encoding="utf-8")
