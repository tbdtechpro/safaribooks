#!/usr/bin/env python3
"""
calibre_convert.py — batch Calibre post-processing for SafariBooks EPUBs.

Converts each EPUB through Calibre's ebook-convert pipeline, which fixes
structural issues and produces a clean, standards-compliant EPUB.

Usage (standalone):
    python calibre_convert.py Books/*/*.epub
    python calibre_convert.py Books/MyBook/9781234567890.epub --add-to-library

Calibre must be installed:
    sudo apt-get install calibre        # Ubuntu/Debian
    brew install --cask calibre         # macOS
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path


EBOOK_CONVERT = "ebook-convert"
CALIBREDB     = "calibredb"


def check_calibre() -> bool:
    """Return True if ebook-convert is on PATH."""
    try:
        subprocess.run(
            [EBOOK_CONVERT, "--version"],
            capture_output=True,
            check=True,
        )
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def convert_epub(
    input_path: str,
    output_path: str | None = None,
    extra_args: list[str] | None = None,
) -> tuple[bool, str]:
    """
    Run ebook-convert on *input_path* and write to *output_path*.

    If *output_path* is None, the output is written next to the input with
    '_calibre' appended before the extension.

    Returns (success, output_path_or_error_message).
    """
    in_path = Path(input_path)
    if not in_path.is_file():
        return False, f"Input file not found: {input_path}"

    if output_path is None:
        output_path = str(in_path.with_stem(in_path.stem + "_calibre"))

    cmd = [
        EBOOK_CONVERT,
        str(in_path),
        output_path,
        "--no-default-epub-cover",
        "--pretty-print-html",
    ]
    if extra_args:
        cmd.extend(extra_args)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except FileNotFoundError:
        return False, f"`{EBOOK_CONVERT}` not found — install Calibre first."
    except subprocess.TimeoutExpired:
        return False, "Calibre conversion timed out (>10 min)."

    if result.returncode != 0:
        err = (result.stderr or result.stdout or "unknown error").strip()
        return False, err

    return True, output_path


def add_to_library(epub_path: str) -> tuple[bool, str]:
    """Add *epub_path* to the default Calibre library."""
    try:
        result = subprocess.run(
            [CALIBREDB, "add", epub_path],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError:
        return False, f"`{CALIBREDB}` not found — install Calibre first."
    except subprocess.TimeoutExpired:
        return False, "calibredb timed out."

    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "unknown error").strip()

    return True, result.stdout.strip()


def batch_convert(
    input_paths: list[str],
    add_library: bool = False,
    verbose: bool = True,
) -> dict[str, dict]:
    """
    Convert a list of EPUB files.

    Returns a dict keyed by input path with result info.
    """
    results = {}

    for path in input_paths:
        if verbose:
            print(f"Converting: {path} … ", end="", flush=True)

        ok, out = convert_epub(path)
        entry = {"input": path, "success": ok}

        if ok:
            entry["output"] = out
            if verbose:
                print(f"✓  →  {out}")

            if add_library:
                lib_ok, lib_out = add_to_library(out)
                entry["library"] = lib_ok
                if verbose:
                    if lib_ok:
                        print(f"   Added to Calibre library: {lib_out}")
                    else:
                        print(f"   Library import failed: {lib_out}")
        else:
            entry["error"] = out
            if verbose:
                print(f"✗  {out}")

        results[path] = entry

    return results


def main():
    ap = argparse.ArgumentParser(
        prog="calibre_convert.py",
        description="Convert SafariBooks EPUBs through Calibre for proper formatting.",
    )
    ap.add_argument(
        "epubs",
        nargs="+",
        metavar="EPUB",
        help="One or more .epub files to convert.",
    )
    ap.add_argument(
        "--add-to-library",
        dest="library",
        action="store_true",
        help="Also import each converted EPUB into the default Calibre library.",
    )
    ap.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-file output.",
    )
    args = ap.parse_args()

    if not check_calibre():
        print(
            "Error: Calibre's `ebook-convert` was not found.\n"
            "Install it with:  sudo apt-get install calibre",
            file=sys.stderr,
        )
        sys.exit(1)

    results = batch_convert(args.epubs, add_library=args.library, verbose=not args.quiet)

    ok_count   = sum(1 for r in results.values() if r["success"])
    fail_count = len(results) - ok_count
    print(f"\nDone: {ok_count} succeeded, {fail_count} failed.")
    sys.exit(0 if fail_count == 0 else 1)


if __name__ == "__main__":
    main()
