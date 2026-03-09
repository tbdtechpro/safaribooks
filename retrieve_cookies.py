#!/usr/bin/env python3
"""
retrieve_cookies.py — save O'Reilly session cookies to cookies.json.

Four modes:

  1. Username + password (recommended):
       python3 retrieve_cookies.py --login "account@mail.com:password"
       python3 retrieve_cookies.py --login  # prompts interactively

  2. Paste the Cookie header from DevTools:
       python3 retrieve_cookies.py --cookie "Cookie: orm-jwt=eyJ...; orm-rt=a8b..."

  3. Read from stdin (pipe-friendly):
       pbpaste | python3 retrieve_cookies.py --stdin
       xclip -o | python3 retrieve_cookies.py --stdin

  4. Auto-extract from your browser (requires browser_cookie3, may fail on
     modern Chrome due to OS-level cookie encryption):
       python3 retrieve_cookies.py

How to get the cookie string (mode 2 / 3):
  - Open Chrome/Firefox DevTools (F12) → Network tab
  - Visit learning.oreilly.com and make sure you are logged in
  - Click any request to learning.oreilly.com → Headers → Request Headers
  - Right-click the "Cookie" row → Copy value  (or copy the whole header line)
  - Paste as the --cookie argument or pipe into --stdin

See: https://github.com/lorenzodifuccia/safaribooks/issues/358
"""

import argparse
import getpass
import json
import sys

try:
    from safaribooks import COOKIES_FILE
except ImportError:
    COOKIES_FILE = "cookies.json"

# Set-Cookie attributes — not real cookie names, skip when parsing a
# "Cookie: " request header that may have been pasted from a Set-Cookie line.
_SKIP = frozenset((
    "path", "domain", "expires", "max-age",
    "samesite", "httponly", "secure", "version",
))


def parse_cookie_string(raw: str) -> dict:
    """Parse a Cookie request-header string into a {name: value} dict.

    Accepts the full header line (with or without the leading "Cookie: "),
    or just the name=value; name2=value2 portion.
    """
    raw = raw.strip()

    # Strip leading header label if the user copied the whole header line.
    for prefix in ("Cookie: ", "cookie: ", "Cookie:", "cookie:"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):].strip()
            break

    if raw.startswith("{"):
        # Already JSON — accept it directly.
        return json.loads(raw)

    cookies = {}
    # Cookies in a request header are separated by "; ".
    # Values may contain "=" (e.g. base64, URL-encoded strings) so we split
    # only on the *first* "=" in each pair.
    for pair in raw.split(";"):
        pair = pair.strip()
        if "=" not in pair:
            continue
        k, _, v = pair.partition("=")
        k = k.strip()
        if k.lower() not in _SKIP and k:
            cookies[k] = v  # preserve value exactly — no strip
    return cookies


def login_with_credentials(email: str, password: str) -> dict:
    """Log in to O'Reilly via the REST API and return the session cookies."""
    try:
        import requests
    except ImportError:
        print(
            "[!] requests is not installed. Install it with: pip install requests",
            file=sys.stderr,
        )
        return {}

    url = "https://api.oreilly.com/api/v1/auth/login/"
    try:
        resp = requests.post(
            url,
            json={"email": email, "password": password},
            headers={"Origin": "https://www.oreilly.com"},
            timeout=30,
        )
    except Exception as exc:
        print(f"[!] Login request failed: {exc}", file=sys.stderr)
        return {}

    if resp.status_code != 200:
        try:
            detail = resp.json().get("detail") or resp.json().get("message") or resp.text[:200]
        except Exception:
            detail = resp.text[:200]
        print(f"[!] Login failed (HTTP {resp.status_code}): {detail}", file=sys.stderr)
        return {}

    data = resp.json()
    if not data.get("logged_in"):
        print("[!] Login failed: server returned logged_in=false.", file=sys.stderr)
        return {}

    cookies = {"orm-jwt": data["id_token"], "orm-rt": data["refresh_token"]}
    return cookies


def get_oreilly_cookies_from_browser() -> dict:
    """Try to load O'Reilly cookies from the local browser profile.

    Requires browser_cookie3. May fail silently on modern Chrome (Linux/macOS)
    when the OS keyring is not accessible from the current session.
    """
    try:
        import browser_cookie3
    except ImportError:
        print(
            "[!] browser_cookie3 is not installed. "
            "Install it with: pip install browser_cookie3\n"
            "    Or use --cookie / --stdin instead.",
            file=sys.stderr,
        )
        return {}

    orly_domains = {"oreilly.com", "learning.oreilly.com", "api.oreilly.com"}
    try:
        cj = browser_cookie3.load(domain_name=".oreilly.com")
    except Exception as exc:
        print(f"[!] browser_cookie3 failed: {exc}", file=sys.stderr)
        return {}

    cookies = {}
    for c in cj:
        domain = c.domain.lstrip(".")
        if domain in orly_domains or domain.endswith(".oreilly.com"):
            if c.value:  # skip cookies that came back empty (encryption failure)
                cookies[c.name] = c.value
    return cookies


def save_cookies(cookies: dict) -> None:
    if not cookies:
        print("[!] No cookies to save.", file=sys.stderr)
        sys.exit(1)

    with open(COOKIES_FILE, "w") as f:
        json.dump(cookies, f)

    has_jwt = "orm-jwt" in cookies
    has_rt  = "orm-rt"  in cookies
    print(f"[+] Saved {len(cookies)} cookie(s) to {COOKIES_FILE}")
    print(f"    orm-jwt present : {'yes' if has_jwt else 'NO — auth will likely fail'}")
    print(f"    orm-rt  present : {'yes' if has_rt  else 'NO — token refresh unavailable'}")
    if has_jwt:
        print("    Note: O'Reilly JWTs expire quickly (~20 min). Start your download now.")


def main():
    parser = argparse.ArgumentParser(
        description="Save O'Reilly session cookies to cookies.json.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  # Login with credentials (recommended):\n"
            "  python3 retrieve_cookies.py --login \"account@mail.com:password\"\n"
            "  python3 retrieve_cookies.py --login  # prompts interactively\n\n"
            "  # Paste from DevTools:\n"
            "  python3 retrieve_cookies.py --cookie \"orm-jwt=eyJ...; orm-rt=a8b...\"\n\n"
            "  # Pipe from clipboard (Linux):\n"
            "  xclip -o | python3 retrieve_cookies.py --stdin\n\n"
            "  # Auto-extract from browser (may fail on modern Chrome):\n"
            "  python3 retrieve_cookies.py\n"
        ),
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--login",
        metavar="EMAIL:PASS",
        nargs="?",
        const="",
        help=(
            "Log in with your O'Reilly credentials. "
            "Pass as 'email:password' or omit the value to be prompted interactively."
        ),
    )
    group.add_argument(
        "--cookie",
        metavar="COOKIE_STRING",
        help=(
            "Cookie header value copied from DevTools. "
            "Accepts the full 'Cookie: name=val; ...' line or just 'name=val; ...'."
        ),
    )
    group.add_argument(
        "--stdin",
        action="store_true",
        help="Read the cookie string from stdin (useful for piping from clipboard tools).",
    )
    args = parser.parse_args()

    if args.login is not None:
        if args.login == "":
            email = input("Email: ").strip()
            password = getpass.getpass("Password: ")
        elif ":" in args.login:
            sep = args.login.index(":")
            email = args.login[:sep]
            password = args.login[sep + 1:]
        else:
            parser.error("--login value must be in 'email:password' format, or omit the value to be prompted.")
        cookies = login_with_credentials(email, password)
    elif args.cookie:
        cookies = parse_cookie_string(args.cookie)
    elif args.stdin:
        raw = sys.stdin.read()
        cookies = parse_cookie_string(raw)
    else:
        print("[*] Attempting to extract cookies from your browser...")
        cookies = get_oreilly_cookies_from_browser()
        if not cookies:
            print(
                "[!] No O'Reilly cookies found in browser.\n"
                "    Make sure you are logged in at learning.oreilly.com,\n"
                "    or use --login / --cookie / --stdin.",
                file=sys.stderr,
            )
            sys.exit(1)

    save_cookies(cookies)


if __name__ == "__main__":
    main()
