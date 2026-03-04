import json

# See: https://github.com/lorenzodifuccia/safaribooks/issues/358

try:
    from safaribooks import COOKIES_FILE
except ImportError:
    COOKIES_FILE = "cookies.json"

try:
    import browser_cookie3
except ImportError:
    raise ImportError(
        "browser_cookie3 is not installed.\n"
        "Install it with: pip install browser_cookie3"
    )

ORLY_DOMAINS = ("oreilly.com", "learning.oreilly.com", "api.oreilly.com")


def get_oreilly_cookies():
    cj = browser_cookie3.load(domain_name=".oreilly.com")
    cookies = {}
    for c in cj:
        if any(c.domain.endswith(d) for d in ORLY_DOMAINS) or c.domain == ".oreilly.com":
            cookies[c.name] = c.value
    return cookies


def main():
    cookies = get_oreilly_cookies()
    if not cookies:
        print("No O'Reilly cookies found. Make sure you are logged in at learning.oreilly.com in your browser.")
        return
    with open(COOKIES_FILE, "w") as f:
        json.dump(cookies, f)
    print(f"Saved {len(cookies)} cookie(s) to {COOKIES_FILE}")


if __name__ == "__main__":
    main()
