#!/usr/bin/env python3
import re
import os
import sys
import json
import shutil
import pathlib
import getpass
import logging
import argparse
import requests
import traceback
from html import escape
from random import random
from typing import Callable, Optional
from lxml import html, etree
from multiprocessing import Process, Queue, Value
from urllib.parse import urljoin, urlparse, parse_qs, quote_plus


class SafariBooksError(Exception):
    """Raised when SafariBooks encounters an unrecoverable error (TUI/API mode)."""


PATH = os.path.dirname(os.path.realpath(__file__))
COOKIES_FILE = os.path.join(PATH, "cookies.json")

ORLY_BASE_HOST = "oreilly.com"  # PLEASE INSERT URL HERE

SAFARI_BASE_HOST = "learning." + ORLY_BASE_HOST
API_ORIGIN_HOST = "api." + ORLY_BASE_HOST

ORLY_BASE_URL = "https://www." + ORLY_BASE_HOST
SAFARI_BASE_URL = "https://" + SAFARI_BASE_HOST
API_ORIGIN_URL = "https://" + API_ORIGIN_HOST
PROFILE_URL = SAFARI_BASE_URL + "/profile/"
API_V2_TEMPLATE = "https://" + SAFARI_BASE_HOST + "/api/v2/epubs/urn:orm:book:{0}/"
API_V2_CHAPTERS_TEMPLATE = "https://" + SAFARI_BASE_HOST + "/api/v2/epub-chapters/?epub_identifier=urn:orm:book:{0}"
LOGIN_ENTRY_URL = SAFARI_BASE_URL + "/login/"

# DEBUG
USE_PROXY = False
PROXIES = {"https": "https://127.0.0.1:8080"}


class Display:
    BASE_FORMAT = logging.Formatter(
        fmt="[%(asctime)s] %(message)s",
        datefmt="%d/%b/%Y %H:%M:%S"
    )

    SH_DEFAULT = "\033[0m" if "win" not in sys.platform else ""  # TODO: colors for Windows
    SH_YELLOW = "\033[33m" if "win" not in sys.platform else ""
    SH_BG_RED = "\033[41m" if "win" not in sys.platform else ""
    SH_BG_YELLOW = "\033[43m" if "win" not in sys.platform else ""

    def __init__(
        self,
        log_file: str,
        progress_callback: Optional[Callable[[str, float], None]] = None,
        raise_on_exit: bool = False,
        quiet: bool = False,
    ):
        self.output_dir = ""
        self.output_dir_set = False
        self.log_file = os.path.join(PATH, log_file)
        self.progress_callback = progress_callback
        self.raise_on_exit = raise_on_exit
        self.quiet = quiet
        self._current_stage = ""

        self.logger = logging.getLogger("SafariBooks.%s" % log_file)
        self.logger.setLevel(logging.INFO)
        logs_handler = logging.FileHandler(filename=self.log_file)
        logs_handler.setFormatter(self.BASE_FORMAT)
        logs_handler.setLevel(logging.INFO)
        self.logger.addHandler(logs_handler)

        self.columns, _ = shutil.get_terminal_size()

        self.logger.info("** Welcome to SafariBooks! **")

        self.book_ad_info = False
        self.css_ad_info = Value("i", 0)
        self.images_ad_info = Value("i", 0)
        self.last_request = (None,)
        self.in_error = False

        self.state_status = Value("i", 0)
        if not quiet:
            sys.excepthook = self.unhandled_exception

    def set_output_dir(self, output_dir):
        self.info("Output directory:\n    %s" % output_dir)
        self.output_dir = output_dir
        self.output_dir_set = True

    def unregister(self):
        self.logger.handlers[0].close()
        if not self.quiet:
            sys.excepthook = sys.__excepthook__

    def log(self, message):
        try:
            self.logger.info(str(message, "utf-8", "replace"))

        except (UnicodeDecodeError, Exception):
            self.logger.info(message)

    def out(self, put):
        if self.quiet:
            return
        pattern = "\r{!s}\r{!s}\n"
        try:
            s = pattern.format(" " * self.columns, str(put, "utf-8", "replace"))

        except TypeError:
            s = pattern.format(" " * self.columns, put)

        sys.stdout.write(s)

    def info(self, message, state=False):
        self._current_stage = message
        self.log(message)
        output = (self.SH_YELLOW + "[*]" + self.SH_DEFAULT if not state else
                  self.SH_BG_YELLOW + "[-]" + self.SH_DEFAULT) + " %s" % message
        self.out(output)
        if self.progress_callback:
            self.progress_callback(message, -1.0)

    def error(self, error):
        if not self.in_error:
            self.in_error = True

        self.log(error)
        output = self.SH_BG_RED + "[#]" + self.SH_DEFAULT + " %s" % error
        self.out(output)

    def exit(self, error):
        self.error(str(error))

        if self.output_dir_set and not self.quiet:
            output = (self.SH_YELLOW + "[+]" + self.SH_DEFAULT +
                      " Please delete the output directory '" + self.output_dir + "'"
                      " and restart the program.")
            self.out(output)

        if not self.quiet:
            output = self.SH_BG_RED + "[!]" + self.SH_DEFAULT + " Aborting..."
            self.out(output)

        self.save_last_request()
        if self.raise_on_exit:
            raise SafariBooksError(str(error))
        sys.exit(1)

    def unhandled_exception(self, _, o, tb):
        self.log("".join(traceback.format_tb(tb)))
        self.exit("Unhandled Exception: %s (type: %s)" % (o, o.__class__.__name__))

    def save_last_request(self):
        if any(self.last_request):
            self.log("Last request done:\n\tURL: {0}\n\tDATA: {1}\n\tOTHERS: {2}\n\n\t{3}\n{4}\n\n{5}\n"
                     .format(*self.last_request))

    def intro(self):
        if self.quiet:
            return
        output = self.SH_YELLOW + (r"""
       ____     ___         _
      / __/__ _/ _/__ _____(_)
     _\ \/ _ `/ _/ _ `/ __/ /
    /___/\_,_/_/ \_,_/_/ /_/
      / _ )___  ___  / /__ ___
     / _  / _ \/ _ \/  '_/(_-<
    /____/\___/\___/_/\_\/___/
""" if random() > 0.5 else r"""
 ██████╗     ██████╗ ██╗  ██╗   ██╗██████╗
██╔═══██╗    ██╔══██╗██║  ╚██╗ ██╔╝╚════██╗
██║   ██║    ██████╔╝██║   ╚████╔╝   ▄███╔╝
██║   ██║    ██╔══██╗██║    ╚██╔╝    ▀▀══╝
╚██████╔╝    ██║  ██║███████╗██║     ██╗
 ╚═════╝     ╚═╝  ╚═╝╚══════╝╚═╝     ╚═╝
""") + self.SH_DEFAULT
        output += "\n" + "~" * (self.columns // 2)

        self.out(output)

    def parse_description(self, desc):
        if not desc:
            return "n/d"

        try:
            return html.fromstring(desc).text_content()

        except (html.etree.ParseError, html.etree.ParserError) as e:
            self.log("Error parsing the description: %s" % e)
            return "n/d"

    def book_info(self, info):
        description = self.parse_description(info.get("description", None)).replace("\n", " ")
        for t in [
            ("Title", info.get("title", "")), ("Authors", ", ".join(aut.get("name", "") for aut in info.get("authors", []))),
            ("Identifier", info.get("identifier", "")), ("ISBN", info.get("isbn", "")),
            ("Publishers", ", ".join(pub.get("name", "") for pub in info.get("publishers", []))),
            ("Rights", info.get("rights", "")),
            ("Description", description[:500] + "..." if len(description) >= 500 else description),
            ("Release Date", info.get("issued", "")),
            ("URL", info.get("web_url", ""))
        ]:
            self.info("{0}{1}{2}: {3}".format(self.SH_YELLOW, t[0], self.SH_DEFAULT, t[1]), True)

    def state(self, origin, done):
        progress = int(done * 100 / origin)
        bar = int(progress * (self.columns - 11) / 100)
        if self.state_status.value < progress:
            self.state_status.value = progress
            if not self.quiet:
                sys.stdout.write(
                    "\r    " + self.SH_BG_YELLOW + "[" + ("#" * bar).ljust(self.columns - 11, "-") + "]" +
                    self.SH_DEFAULT + ("%4s" % progress) + "%" + ("\n" if progress == 100 else "")
                )
            if self.progress_callback:
                self.progress_callback(self._current_stage, progress / 100.0)

    def done(self, epub_file):
        self.info("Done: %s\n\n" % epub_file +
                  "    If you like it, please * this project on GitHub to make it known:\n"
                  "        https://github.com/lorenzodifuccia/safaribooks\n"
                  "    e don't forget to renew your Safari Books Online subscription:\n"
                  "        " + SAFARI_BASE_URL + "\n\n" +
                  self.SH_BG_RED + "[!]" + self.SH_DEFAULT + " Bye!!")

    @staticmethod
    def api_error(response):
        message = "API: "
        if "detail" in response and "Not found" in response["detail"]:
            message += "book's not present in Safari Books Online.\n" \
                       "    The book identifier is the digits that you can find in the URL:\n" \
                       "    `" + SAFARI_BASE_URL + "/library/view/book-name/XXXXXXXXXXXXX/`"

        else:
            os.remove(COOKIES_FILE)
            detail = " (%s)" % response["detail"] if "detail" in response else ""
            message += (
                "Out-of-Session%s.\n" % detail +
                Display.SH_YELLOW + "[+]" + Display.SH_DEFAULT +
                " Please save fresh cookies to `cookies.json` and try again.\n"
                "    See: https://github.com/lorenzodifuccia/safaribooks/issues/358"
            )

        return message


class WinQueue(list):  # TODO: error while use `process` in Windows: can't pickle _thread.RLock objects
    def put(self, el):
        self.append(el)

    def qsize(self):
        return self.__len__()


class SafariBooks:
    LOGIN_URL = API_ORIGIN_URL + "/api/v1/auth/login/"

    API_TEMPLATE = SAFARI_BASE_URL + "/api/v1/book/{0}/"

    BASE_01_HTML = "<!DOCTYPE html>\n" \
                   "<html lang=\"en\" xml:lang=\"en\" xmlns=\"http://www.w3.org/1999/xhtml\"" \
                   " xmlns:xsi=\"http://www.w3.org/2001/XMLSchema-instance\"" \
                   " xsi:schemaLocation=\"http://www.w3.org/2002/06/xhtml2/" \
                   " http://www.w3.org/MarkUp/SCHEMA/xhtml2.xsd\"" \
                   " xmlns:epub=\"http://www.idpf.org/2007/ops\">\n" \
                   "<head>\n" \
                   "{0}\n" \
                   "<style type=\"text/css\">" \
                   "body{{margin:1em;background-color:transparent!important;}}" \
                   "#sbo-rt-content *{{text-indent:0pt!important;}}#sbo-rt-content .bq{{margin-right:1em!important;}}"

    KINDLE_HTML = "#sbo-rt-content *{{word-wrap:break-word!important;" \
                  "word-break:break-word!important;}}#sbo-rt-content table,#sbo-rt-content pre" \
                  "{{overflow-x:unset!important;overflow:unset!important;" \
                  "overflow-y:unset!important;white-space:pre-wrap!important;}}"

    BASE_02_HTML = "</style>" \
                   "</head>\n" \
                   "<body>{1}</body>\n</html>"

    CONTAINER_XML = "<?xml version=\"1.0\"?>" \
                    "<container version=\"1.0\" xmlns=\"urn:oasis:names:tc:opendocument:xmlns:container\">" \
                    "<rootfiles>" \
                    "<rootfile full-path=\"OEBPS/content.opf\" media-type=\"application/oebps-package+xml\" />" \
                    "</rootfiles>" \
                    "</container>"

    # Format: ID, Title, Authors, Description, Subjects, Publisher, Rights, Date, CoverId, MANIFEST, SPINE, CoverUrl
    CONTENT_OPF = "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n" \
                  "<package xmlns=\"http://www.idpf.org/2007/opf\" unique-identifier=\"bookid\" version=\"2.0\" >\n" \
                  "<metadata xmlns:dc=\"http://purl.org/dc/elements/1.1/\" " \
                  " xmlns:opf=\"http://www.idpf.org/2007/opf\">\n" \
                  "<dc:title>{1}</dc:title>\n" \
                  "{2}\n" \
                  "<dc:description>{3}</dc:description>\n" \
                  "{4}" \
                  "<dc:publisher>{5}</dc:publisher>\n" \
                  "<dc:rights>{6}</dc:rights>\n" \
                  "<dc:language>en-US</dc:language>\n" \
                  "<dc:date>{7}</dc:date>\n" \
                  "<dc:identifier id=\"bookid\">{0}</dc:identifier>\n" \
                  "<meta name=\"cover\" content=\"{8}\"/>\n" \
                  "</metadata>\n" \
                  "<manifest>\n" \
                  "<item id=\"ncx\" href=\"toc.ncx\" media-type=\"application/x-dtbncx+xml\" />\n" \
                  "{9}\n" \
                  "</manifest>\n" \
                  "<spine toc=\"ncx\">\n{10}</spine>\n" \
                  "<guide><reference href=\"{11}\" title=\"Cover\" type=\"cover\" /></guide>\n" \
                  "</package>"

    # Format: ID, Depth, Title, Author, NAVMAP
    TOC_NCX = "<?xml version=\"1.0\" encoding=\"utf-8\" standalone=\"no\" ?>\n" \
              "<!DOCTYPE ncx PUBLIC \"-//NISO//DTD ncx 2005-1//EN\"" \
              " \"http://www.daisy.org/z3986/2005/ncx-2005-1.dtd\">\n" \
              "<ncx xmlns=\"http://www.daisy.org/z3986/2005/ncx/\" version=\"2005-1\">\n" \
              "<head>\n" \
              "<meta content=\"ID:ISBN:{0}\" name=\"dtb:uid\"/>\n" \
              "<meta content=\"{1}\" name=\"dtb:depth\"/>\n" \
              "<meta content=\"0\" name=\"dtb:totalPageCount\"/>\n" \
              "<meta content=\"0\" name=\"dtb:maxPageNumber\"/>\n" \
              "</head>\n" \
              "<docTitle><text>{2}</text></docTitle>\n" \
              "<docAuthor><text>{3}</text></docAuthor>\n" \
              "<navMap>{4}</navMap>\n" \
              "</ncx>"

    HEADERS = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Referer": LOGIN_ENTRY_URL,
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/90.0.4430.212 Safari/537.36"
    }

    COOKIE_FLOAT_MAX_AGE_PATTERN = re.compile(r'(max-age=\d*\.\d*)', re.IGNORECASE)

    def __init__(
        self,
        args,
        progress_callback: Optional[Callable[[str, float], None]] = None,
        raise_on_exit: bool = False,
        quiet: bool = False,
    ):
        self.args = args
        self.display = Display(
            "info_%s.log" % escape(args.bookid),
            progress_callback=progress_callback,
            raise_on_exit=raise_on_exit,
            quiet=quiet,
        )
        self.display.intro()

        self.session = requests.Session()
        if USE_PROXY:  # DEBUG
            self.session.proxies = PROXIES
            self.session.verify = False

        self.session.headers.update(self.HEADERS)

        self.jwt = {}

        if not args.cred:
            if not os.path.isfile(COOKIES_FILE):
                self.display.exit("Login: unable to find `cookies.json` file.\n"
                                  "    Please use the `--cred` or `--login` options to perform the login.")

            with open(COOKIES_FILE) as f:
                self.session.cookies.update(json.load(f))

        else:
            self.display.info("Logging into Safari Books Online...", state=True)
            self.do_login(*args.cred)
            if not args.no_cookies:
                with open(COOKIES_FILE, "w") as f:
                    json.dump(self.session.cookies.get_dict(), f)

        self.check_login()

        self.book_id = args.bookid
        self.api_v2 = False
        self.api_url = self.API_TEMPLATE.format(self.book_id)

        self.display.info("Retrieving book info...")
        self.book_info = self.get_book_info()
        self.display.book_info(self.book_info)

        self.display.info("Retrieving book chapters...")
        self.book_chapters = self.get_book_chapters()

        self.chapters_queue = self.book_chapters[:]

        if len(self.book_chapters) > sys.getrecursionlimit():
            sys.setrecursionlimit(len(self.book_chapters))

        self.book_title = self.book_info["title"]
        self.base_url = self.book_info["web_url"]

        self.clean_book_title = "".join(self.escape_dirname(self.book_title).split(",")[:2]) \
                                + " ({0})".format(self.book_id)

        books_dir = os.path.join(PATH, "Books")
        if not os.path.isdir(books_dir):
            os.mkdir(books_dir)

        self.BOOK_PATH = os.path.join(books_dir, self.clean_book_title)
        self.display.set_output_dir(self.BOOK_PATH)
        self.css_path = ""
        self.images_path = ""
        self.create_dirs()

        self.chapter_title = ""
        self.filename = ""
        self.chapter_stylesheets = []
        self.css = []
        self.images = []

        self.display.info("Downloading book contents... (%s chapters)" % len(self.book_chapters), state=True)
        self.BASE_HTML = self.BASE_01_HTML + (self.KINDLE_HTML if not args.kindle else "") + self.BASE_02_HTML

        self.cover = False
        self.get()
        if not self.cover:
            self.cover = self.get_default_cover() if "cover" in self.book_info else False
            cover_html = self.parse_html(
                html.fromstring("<div id=\"sbo-rt-content\"><img src=\"Images/{0}\"></div>".format(self.cover)), True
            )

            self.book_chapters = [{
                "filename": "default_cover.xhtml",
                "title": "Cover"
            }] + self.book_chapters

            self.filename = self.book_chapters[0]["filename"]
            self.save_page_html(cover_html)

        self.css_done_queue = Queue(0) if "win" not in sys.platform else WinQueue()
        self.display.info("Downloading book CSSs... (%s files)" % len(self.css), state=True)
        self.collect_css()
        self.images_done_queue = Queue(0) if "win" not in sys.platform else WinQueue()
        self.display.info("Downloading book images... (%s files)" % len(self.images), state=True)
        self.collect_images()

        self.display.info("Creating EPUB file...", state=True)
        self.create_epub()

        if not args.no_cookies:
            with open(COOKIES_FILE, "w") as f:
                json.dump(self.session.cookies.get_dict(), f)

        self.epub_path = os.path.join(self.BOOK_PATH, self.book_id + ".epub")
        self.display.done(self.epub_path)

        if not self.display.in_error:
            self._post_download_exports()

        self.display.unregister()

        if not self.display.in_error and not args.log:
            os.remove(self.display.log_file)

    def handle_cookie_update(self, set_cookie_headers):
        for morsel in set_cookie_headers:
            # Handle Float 'max-age' Cookie
            if self.COOKIE_FLOAT_MAX_AGE_PATTERN.search(morsel):
                cookie_key, cookie_value = morsel.split(";")[0].split("=")
                self.session.cookies.set(cookie_key, cookie_value)

    def requests_provider(self, url, is_post=False, data=None, perform_redirect=True, **kwargs):
        try:
            response = getattr(self.session, "post" if is_post else "get")(
                url,
                data=data,
                allow_redirects=False,
                **kwargs
            )

            self.handle_cookie_update(response.raw.headers.getlist("Set-Cookie"))

            self.display.last_request = (
                url, data, kwargs, response.status_code, "\n".join(
                    ["\t{}: {}".format(*h) for h in response.headers.items()]
                ), response.text
            )

            self.display.log(
                "HTTP %d  %s  [%d bytes]" % (response.status_code, url, len(response.content))
            )

        except (requests.ConnectionError, requests.ConnectTimeout, requests.RequestException) as request_exception:
            self.display.error(str(request_exception))
            return 0

        if response.is_redirect and perform_redirect:
            return self.requests_provider(response.next.url, is_post, None, perform_redirect)
            # TODO How about **kwargs?

        return response

    @staticmethod
    def parse_cred(cred):
        if ":" not in cred:
            return False

        sep = cred.index(":")
        new_cred = ["", ""]
        new_cred[0] = cred[:sep].strip("'").strip('"')
        if "@" not in new_cred[0]:
            return False

        new_cred[1] = cred[sep + 1:]
        return new_cred

    def do_login(self, email, password):
        response = self.requests_provider(
            self.LOGIN_URL,
            is_post=True,
            json={"email": email, "password": password},
        )

        if response == 0:
            self.display.exit("Login: unable to reach O'Reilly API. Try again...")

        if response.status_code != 200:
            try:
                error = response.json()
                detail = error.get("detail") or error.get("message") or str(error)
            except Exception:
                detail = response.text[:200]
            self.display.exit("Login: failed — %s" % detail)

        data = response.json()
        if not data.get("logged_in"):
            self.display.exit("Login: server returned logged_in=false. Check your credentials.")

        # Cookies (orm-jwt, orm-rt) are set via Set-Cookie headers on api.oreilly.com.
        # Explicitly mirror them onto the session for learning.oreilly.com requests.
        self.session.cookies.set("orm-jwt", data["id_token"], domain=".oreilly.com")
        self.session.cookies.set("orm-rt", data["refresh_token"], domain=".oreilly.com")

    def check_login(self):
        response = self.requests_provider(PROFILE_URL, perform_redirect=False)

        if response == 0:
            self.display.exit("Login: unable to reach Safari Books Online. Try again...")

        elif response.status_code != 200:
            self.display.exit("Authentication issue: unable to access profile page.")

        elif "user_type\":\"Expired\"" in response.text:
            self.display.exit("Authentication issue: account subscription expired.")

        self.display.info("Successfully authenticated.", state=True)

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

    def get_default_cover(self):
        response = self.requests_provider(self.book_info["cover"], stream=True)
        if response == 0:
            self.display.error("Error trying to retrieve the cover: %s" % self.book_info["cover"])
            return False

        file_ext = response.headers["Content-Type"].split("/")[-1]
        with open(os.path.join(self.images_path, "default_cover." + file_ext), 'wb') as i:
            for chunk in response.iter_content(1024):
                i.write(chunk)

        return "default_cover." + file_ext

    def get_html(self, url):
        response = self.requests_provider(url)
        if response == 0 or response.status_code != 200:
            self.display.exit(
                "Crawler: error trying to retrieve this page: %s (%s)\n    From: %s" %
                (self.filename, self.chapter_title, url)
            )

        root = None
        try:
            root = html.fromstring(response.text, base_url=SAFARI_BASE_URL)

        except (html.etree.ParseError, html.etree.ParserError) as parsing_error:
            self.display.error(parsing_error)
            self.display.exit(
                "Crawler: error trying to parse this page: %s (%s)\n    From: %s" %
                (self.filename, self.chapter_title, url)
            )

        return root

    @staticmethod
    def url_is_absolute(url):
        return bool(urlparse(url).netloc)

    @staticmethod
    def is_image_link(url: str):
        return pathlib.Path(url).suffix[1:].lower() in ["jpg", "jpeg", "png", "gif"]

    def link_replace(self, link):
        if link and not link.startswith("mailto"):
            if not self.url_is_absolute(link):
                if any(x in link for x in ["cover", "images", "graphics"]) or \
                        self.is_image_link(link):
                    image = link.split("/")[-1]
                    return "Images/" + image

                return link.replace(".html", ".xhtml")

            else:
                if self.book_id in link:
                    return self.link_replace(link.split(self.book_id)[-1])

        return link

    @staticmethod
    def get_cover(html_root):
        lowercase_ns = etree.FunctionNamespace(None)
        lowercase_ns["lower-case"] = lambda _, n: n[0].lower() if n and len(n) else ""

        images = html_root.xpath("//img[contains(lower-case(@id), 'cover') or contains(lower-case(@class), 'cover') or"
                                 "contains(lower-case(@name), 'cover') or contains(lower-case(@src), 'cover') or"
                                 "contains(lower-case(@alt), 'cover')]")
        if len(images):
            return images[0]

        divs = html_root.xpath("//div[contains(lower-case(@id), 'cover') or contains(lower-case(@class), 'cover') or"
                               "contains(lower-case(@name), 'cover') or contains(lower-case(@src), 'cover')]//img")
        if len(divs):
            return divs[0]

        a = html_root.xpath("//a[contains(lower-case(@id), 'cover') or contains(lower-case(@class), 'cover') or"
                            "contains(lower-case(@name), 'cover') or contains(lower-case(@src), 'cover')]//img")
        if len(a):
            return a[0]

        return None

    def parse_html(self, root, first_page=False):
        if random() > 0.8:
            if len(root.xpath("//div[@class='controls']/a/text()")):
                self.display.exit(self.display.api_error(" "))

        book_content = root.xpath("//div[@id='sbo-rt-content']")
        if not len(book_content):
            self.display.exit(
                "Parser: book content's corrupted or not present: %s (%s)" %
                (self.filename, self.chapter_title)
            )

        page_css = ""
        if len(self.chapter_stylesheets):
            for chapter_css_url in self.chapter_stylesheets:
                if chapter_css_url not in self.css:
                    self.css.append(chapter_css_url)
                    self.display.log("Crawler: found a new CSS at %s" % chapter_css_url)

                page_css += "<link href=\"Styles/Style{0:0>2}.css\" " \
                            "rel=\"stylesheet\" type=\"text/css\" />\n".format(self.css.index(chapter_css_url))

        stylesheet_links = root.xpath("//link[@rel='stylesheet']")
        if len(stylesheet_links):
            for s in stylesheet_links:
                css_url = urljoin("https:", s.attrib["href"]) if s.attrib["href"][:2] == "//" \
                    else urljoin(self.base_url, s.attrib["href"])

                if css_url not in self.css:
                    self.css.append(css_url)
                    self.display.log("Crawler: found a new CSS at %s" % css_url)

                page_css += "<link href=\"Styles/Style{0:0>2}.css\" " \
                            "rel=\"stylesheet\" type=\"text/css\" />\n".format(self.css.index(css_url))

        stylesheets = root.xpath("//style")
        if len(stylesheets):
            for css in stylesheets:
                if "data-template" in css.attrib and len(css.attrib["data-template"]):
                    css.text = css.attrib["data-template"]
                    del css.attrib["data-template"]

                try:
                    page_css += html.tostring(css, method="xml", encoding='unicode') + "\n"

                except (html.etree.ParseError, html.etree.ParserError) as parsing_error:
                    self.display.error(parsing_error)
                    self.display.exit(
                        "Parser: error trying to parse one CSS found in this page: %s (%s)" %
                        (self.filename, self.chapter_title)
                    )

        # TODO: add all not covered tag for `link_replace` function
        svg_image_tags = root.xpath("//image")
        if len(svg_image_tags):
            for img in svg_image_tags:
                image_attr_href = [x for x in img.attrib.keys() if "href" in x]
                if len(image_attr_href):
                    svg_url = img.attrib.get(image_attr_href[0])
                    svg_root = img.getparent().getparent()
                    new_img = svg_root.makeelement("img")
                    new_img.attrib.update({"src": svg_url})
                    svg_root.remove(img.getparent())
                    svg_root.append(new_img)

        book_content = book_content[0]
        book_content.rewrite_links(self.link_replace)

        xhtml = None
        try:
            if first_page:
                is_cover = self.get_cover(book_content)
                if is_cover is not None:
                    page_css = "<style>" \
                               "body{display:table;position:absolute;margin:0!important;height:100%;width:100%;}" \
                               "#Cover{display:table-cell;vertical-align:middle;text-align:center;}" \
                               "img{height:90vh;margin-left:auto;margin-right:auto;}" \
                               "</style>"
                    cover_html = html.fromstring("<div id=\"Cover\"></div>")
                    cover_div = cover_html.xpath("//div")[0]
                    cover_img = cover_div.makeelement("img")
                    cover_img.attrib.update({"src": is_cover.attrib["src"]})
                    cover_div.append(cover_img)
                    book_content = cover_html

                    self.cover = is_cover.attrib["src"]

            xhtml = html.tostring(book_content, method="xml", encoding='unicode')

        except (html.etree.ParseError, html.etree.ParserError) as parsing_error:
            self.display.error(parsing_error)
            self.display.exit(
                "Parser: error trying to parse HTML of this page: %s (%s)" %
                (self.filename, self.chapter_title)
            )

        return page_css, xhtml

    @staticmethod
    def escape_dirname(dirname, clean_space=False):
        if ":" in dirname:
            if dirname.index(":") > 15:
                dirname = dirname.split(":")[0]

            elif "win" in sys.platform:
                dirname = dirname.replace(":", ",")

        for ch in ['~', '#', '%', '&', '*', '{', '}', '\\', '<', '>', '?', '/', '`', '\'', '"', '|', '+', ':']:
            if ch in dirname:
                dirname = dirname.replace(ch, "_")

        return dirname if not clean_space else dirname.replace(" ", "")

    def create_dirs(self):
        if os.path.isdir(self.BOOK_PATH):
            self.display.log("Book directory already exists: %s" % self.BOOK_PATH)

        else:
            os.makedirs(self.BOOK_PATH)

        oebps = os.path.join(self.BOOK_PATH, "OEBPS")
        if not os.path.isdir(oebps):
            self.display.book_ad_info = True
            os.makedirs(oebps)

        self.css_path = os.path.join(oebps, "Styles")
        if os.path.isdir(self.css_path):
            self.display.log("CSSs directory already exists: %s" % self.css_path)

        else:
            os.makedirs(self.css_path)
            self.display.css_ad_info.value = 1

        self.images_path = os.path.join(oebps, "Images")
        if os.path.isdir(self.images_path):
            self.display.log("Images directory already exists: %s" % self.images_path)

        else:
            os.makedirs(self.images_path)
            self.display.images_ad_info.value = 1

    def save_page_html(self, contents):
        self.filename = self.filename.replace(".html", ".xhtml")
        with open(os.path.join(self.BOOK_PATH, "OEBPS", self.filename), "wb") as f:
            f.write(self.BASE_HTML.format(contents[0], contents[1]).encode("utf-8", "xmlcharrefreplace"))
        self.display.log("Created: %s" % self.filename)

    def get(self):
        len_books = len(self.book_chapters)

        for _ in range(len_books):
            if not len(self.chapters_queue):
                return

            first_page = len_books == len(self.chapters_queue)

            next_chapter = self.chapters_queue.pop(0)
            self.chapter_title = next_chapter["title"]
            self.filename = next_chapter["filename"]

            asset_base_url = next_chapter['asset_base_url']
            api_v2_detected = False
            if 'v2' in next_chapter['content']:
                asset_base_url = SAFARI_BASE_URL + "/api/v2/epubs/urn:orm:book:{}/files".format(self.book_id)
                api_v2_detected = True

            if "images" in next_chapter and len(next_chapter["images"]):
                for img_url in next_chapter['images']:
                    if api_v2_detected:
                        self.images.append(asset_base_url + '/' + img_url)
                    else:
                        self.images.append(urljoin(next_chapter['asset_base_url'], img_url))


            # Stylesheets
            self.chapter_stylesheets = []
            if "stylesheets" in next_chapter and len(next_chapter["stylesheets"]):
                self.chapter_stylesheets.extend(x["url"] for x in next_chapter["stylesheets"])

            if "site_styles" in next_chapter and len(next_chapter["site_styles"]):
                self.chapter_stylesheets.extend(next_chapter["site_styles"])

            if os.path.isfile(os.path.join(self.BOOK_PATH, "OEBPS", self.filename.replace(".html", ".xhtml"))):
                if not self.display.book_ad_info and \
                        next_chapter not in self.book_chapters[:self.book_chapters.index(next_chapter)]:
                    self.display.info(
                        ("File `%s` already exists.\n"
                         "    If you want to download again all the book,\n"
                         "    please delete the output directory '" + self.BOOK_PATH + "' and restart the program.")
                         % self.filename.replace(".html", ".xhtml")
                    )
                    self.display.book_ad_info = 2

            else:
                self.save_page_html(self.parse_html(self.get_html(next_chapter["content"]), first_page))

            self.display.state(len_books, len_books - len(self.chapters_queue))

    def _thread_download_css(self, url):
        css_file = os.path.join(self.css_path, "Style{0:0>2}.css".format(self.css.index(url)))
        if os.path.isfile(css_file):
            if not self.display.css_ad_info.value and url not in self.css[:self.css.index(url)]:
                self.display.info(("File `%s` already exists.\n"
                                   "    If you want to download again all the CSSs,\n"
                                   "    please delete the output directory '" + self.BOOK_PATH + "'"
                                   " and restart the program.") %
                                  css_file)
                self.display.css_ad_info.value = 1

        else:
            response = self.requests_provider(url)
            if response == 0:
                self.display.error("Error trying to retrieve this CSS: %s\n    From: %s" % (css_file, url))

            with open(css_file, 'wb') as s:
                s.write(response.content)

        self.css_done_queue.put(1)
        self.display.state(len(self.css), self.css_done_queue.qsize())


    def _thread_download_images(self, url):
        image_name = url.split("/")[-1]
        image_path = os.path.join(self.images_path, image_name)
        if os.path.isfile(image_path):
            if not self.display.images_ad_info.value and url not in self.images[:self.images.index(url)]:
                self.display.info(("File `%s` already exists.\n"
                                   "    If you want to download again all the images,\n"
                                   "    please delete the output directory '" + self.BOOK_PATH + "'"
                                   " and restart the program.") %
                                  image_name)
                self.display.images_ad_info.value = 1

        else:
            response = self.requests_provider(urljoin(SAFARI_BASE_URL, url), stream=True)
            if response == 0:
                self.display.error("Error trying to retrieve this image: %s\n    From: %s" % (image_name, url))
                return

            with open(image_path, 'wb') as img:
                for chunk in response.iter_content(1024):
                    img.write(chunk)

        self.images_done_queue.put(1)
        self.display.state(len(self.images), self.images_done_queue.qsize())

    def _start_multiprocessing(self, operation, full_queue):
        if len(full_queue) > 5:
            for i in range(0, len(full_queue), 5):
                self._start_multiprocessing(operation, full_queue[i:i + 5])

        else:
            process_queue = [Process(target=operation, args=(arg,)) for arg in full_queue]
            for proc in process_queue:
                proc.start()

            for proc in process_queue:
                proc.join()

    def collect_css(self):
        self.display.state_status.value = -1

        # "self._start_multiprocessing" seems to cause problem. Switching to mono-thread download.
        for css_url in self.css:
            self._thread_download_css(css_url)

    def collect_images(self):
        if self.display.book_ad_info == 2:
            self.display.info("Some of the book contents were already downloaded.\n"
                              "    If you want to be sure that all the images will be downloaded,\n"
                              "    please delete the output directory '" + self.BOOK_PATH +
                              "' and restart the program.")

        self.display.state_status.value = -1

        # "self._start_multiprocessing" seems to cause problem. Switching to mono-thread download.
        for image_url in self.images:
            self._thread_download_images(image_url)

    def create_content_opf(self):
        self.css = next(os.walk(self.css_path))[2]
        self.images = next(os.walk(self.images_path))[2]

        manifest = []
        spine = []
        for c in self.book_chapters:
            c["filename"] = c["filename"].replace(".html", ".xhtml")
            item_id = escape("".join(c["filename"].split(".")[:-1]))
            manifest.append("<item id=\"{0}\" href=\"{1}\" media-type=\"application/xhtml+xml\" />".format(
                item_id, c["filename"]
            ))
            spine.append("<itemref idref=\"{0}\"/>".format(item_id))

        for i in set(self.images):
            dot_split = i.split(".")
            head = "img_" + escape("".join(dot_split[:-1]))
            extension = dot_split[-1]
            manifest.append("<item id=\"{0}\" href=\"Images/{1}\" media-type=\"image/{2}\" />".format(
                head, i, "jpeg" if "jp" in extension else extension
            ))

        for i in range(len(self.css)):
            manifest.append("<item id=\"style_{0:0>2}\" href=\"Styles/Style{0:0>2}.css\" "
                            "media-type=\"text/css\" />".format(i))

        authors = "\n".join("<dc:creator opf:file-as=\"{0}\" opf:role=\"aut\">{0}</dc:creator>".format(
            escape(aut.get("name", "n/d"))
        ) for aut in self.book_info.get("authors", []))

        subjects = "\n".join("<dc:subject>{0}</dc:subject>".format(escape(sub.get("name", "n/d")))
                             for sub in self.book_info.get("subjects", []))

        return self.CONTENT_OPF.format(
            (self.book_info.get("isbn",  self.book_id)),
            escape(self.book_title),
            authors,
            escape(self.book_info.get("description", "")),
            subjects,
            ", ".join(escape(pub.get("name", "")) for pub in self.book_info.get("publishers", [])),
            escape(self.book_info.get("rights", "")),
            self.book_info.get("issued", ""),
            self.cover,
            "\n".join(manifest),
            "\n".join(spine),
            self.book_chapters[0]["filename"].replace(".html", ".xhtml")
        )

    @staticmethod
    def parse_toc(l, c=0, mx=0):
        r = ""
        for cc in l:
            c += 1
            if int(cc["depth"]) > mx:
                mx = int(cc["depth"])

            r += "<navPoint id=\"{0}\" playOrder=\"{1}\">" \
                 "<navLabel><text>{2}</text></navLabel>" \
                 "<content src=\"{3}\"/>".format(
                    cc["fragment"] if len(cc["fragment"]) else cc["id"], c,
                    escape(cc["label"]), cc["href"].replace(".html", ".xhtml").split("/")[-1]
                 )

            if cc["children"]:
                sr, c, mx = SafariBooks.parse_toc(cc["children"], c, mx)
                r += sr

            r += "</navPoint>\n"

        return r, c, mx

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
            if not isinstance(response, list):
                self.display.exit(self.display.api_error(response) +
                                  " Don't delete any files, just run again this program"
                                  " in order to complete the `.epub` creation!")
            response = [self._normalize_v2_toc_entry(e) for e in response]
        elif not isinstance(response, list) and len(response.keys()) == 1:
            self.display.exit(
                self.display.api_error(response) +
                " Don't delete any files, just run again this program"
                " in order to complete the `.epub` creation!"
            )

        self._toc_data = response
        navmap, _, max_depth = self.parse_toc(response)
        return self.TOC_NCX.format(
            (self.book_info["isbn"] if self.book_info["isbn"] else self.book_id),
            max_depth,
            self.book_title,
            ", ".join(aut.get("name", "") for aut in self.book_info.get("authors", [])),
            navmap
        )

    def create_epub(self):
        with open(os.path.join(self.BOOK_PATH, "mimetype"), "w") as f:
            f.write("application/epub+zip")
        meta_info = os.path.join(self.BOOK_PATH, "META-INF")
        if os.path.isdir(meta_info):
            self.display.log("META-INF directory already exists: %s" % meta_info)

        else:
            os.makedirs(meta_info)

        with open(os.path.join(meta_info, "container.xml"), "wb") as f:
            f.write(self.CONTAINER_XML.encode("utf-8", "xmlcharrefreplace"))
        with open(os.path.join(self.BOOK_PATH, "OEBPS", "content.opf"), "wb") as f:
            f.write(self.create_content_opf().encode("utf-8", "xmlcharrefreplace"))
        with open(os.path.join(self.BOOK_PATH, "OEBPS", "toc.ncx"), "wb") as f:
            f.write(self.create_toc().encode("utf-8", "xmlcharrefreplace"))

        zip_file = os.path.join(PATH, "Books", self.book_id)
        if os.path.isfile(zip_file + ".zip"):
            os.remove(zip_file + ".zip")

        shutil.make_archive(zip_file, 'zip', self.BOOK_PATH)
        os.rename(zip_file + ".zip", os.path.join(self.BOOK_PATH, self.book_id) + ".epub")

    def _post_download_exports(self):
        """Run optional post-download exports based on CLI flags."""
        args = self.args
        books_dir = os.path.join(PATH, "Books")
        db_path = os.path.join(books_dir, "library.db")

        do_registry = True  # always record downloads
        do_markdown = getattr(args, "export_markdown", False)
        do_db = getattr(args, "export_db", False)
        do_rag = getattr(args, "export_rag", False)

        # RAG implies content DB
        if do_rag:
            do_db = True

        api_version = "v2" if self.api_v2 else "v1"

        from library import BookRegistry
        reg = BookRegistry(db_path)

        # Always record the download
        reg.record_download(
            book_info=self.book_info,
            epub_path=self.epub_path,
            book_dir=self.BOOK_PATH,
            chapters=self.book_chapters,
            api_version=api_version,
        )
        self.display.info("Registry: download recorded in library.db")

        # Feature 2 — Markdown export
        markdown_map = None
        if do_markdown:
            from exporters import MarkdownExporter
            self.display.info("Exporting Markdown...", state=True)
            exporter = MarkdownExporter(
                book_id=self.book_id,
                book_path=self.BOOK_PATH,
                book_info=self.book_info,
                chapters=self.book_chapters,
            )
            markdown_map = exporter.export()
            self.display.info("Markdown export complete: %s/markdown/" % self.BOOK_PATH)

        # Feature 3 — Content DB
        if do_db:
            reg.store_chapters(
                book_id=self.book_id,
                chapters=self.book_chapters,
                book_path=self.BOOK_PATH,
                markdown_map=markdown_map,
            )
            toc_data = getattr(self, "_toc_data", None)
            if toc_data:
                reg.store_toc(self.book_id, toc_data)
            self.display.info("Content DB: chapters/TOC stored in library.db")

        # Feature 4 — RAG JSONL export
        if do_rag:
            from exporters import RagExporter
            rag_dir = os.path.join(self.BOOK_PATH, "rag")
            os.makedirs(rag_dir, exist_ok=True)
            output_path = os.path.join(rag_dir, self.book_id + "_rag.jsonl")
            self.display.info("Exporting RAG JSONL...", state=True)
            exporter = RagExporter(
                book_id=self.book_id,
                book_info=self.book_info,
                chapters=self.book_chapters,
                book_path=self.BOOK_PATH,
                markdown_map=markdown_map,
            )
            exporter.export(output_path)
            self.display.info("RAG export complete: %s" % output_path)

        reg.close()


# MAIN
if __name__ == "__main__":
    arguments = argparse.ArgumentParser(prog="safaribooks.py",
                                        description="Download and generate an EPUB of your favorite books"
                                                    " from Safari Books Online.",
                                        add_help=False,
                                        allow_abbrev=False)

    login_arg_group = arguments.add_mutually_exclusive_group()
    login_arg_group.add_argument(
        "--cred", metavar="<EMAIL:PASS>", default=False,
        help="Credentials used to perform the auth login on Safari Books Online."
             " Es. ` --cred \"account_mail@mail.com:password01\" `."
    )
    login_arg_group.add_argument(
        "--login", action='store_true',
        help="Prompt for credentials used to perform the auth login on Safari Books Online."
    )

    arguments.add_argument(
        "--no-cookies", dest="no_cookies", action='store_true',
        help="Prevent your session data to be saved into `cookies.json` file."
    )
    arguments.add_argument(
        "--kindle", dest="kindle", action='store_true',
        help="Add some CSS rules that block overflow on `table` and `pre` elements."
             " Use this option if you're going to export the EPUB to E-Readers like Amazon Kindle."
    )
    arguments.add_argument(
        "--preserve-log", dest="log", action='store_true', help="Leave the `info_XXXXXXXXXXXXX.log`"
                                                                " file even if there isn't any error."
    )
    arguments.add_argument("--help", action="help", default=argparse.SUPPRESS, help='Show this help message.')

    # --- Library / export flags ---
    arguments.add_argument(
        "--skip-if-downloaded", dest="skip_if_downloaded", action='store_true',
        help="Skip download if the book is already recorded in the local library registry."
    )
    arguments.add_argument(
        "--scan-library", dest="scan_library", action='store_true',
        help="Scan existing Books/ directories and populate library.db, then exit."
    )
    arguments.add_argument(
        "--export-markdown", dest="export_markdown", action='store_true',
        help="Write a GFM Markdown version of the book into Books/{title}/markdown/."
    )
    arguments.add_argument(
        "--export-db", dest="export_db", action='store_true',
        help="Store chapter XHTML and TOC data in Books/library.db."
    )
    arguments.add_argument(
        "--export-rag", dest="export_rag", action='store_true',
        help="Write heading-chunked JSONL to Books/{title}/rag/{book_id}_rag.jsonl."
             " Implies --export-db."
    )

    arguments.add_argument(
        "bookid", metavar='<BOOK ID>', nargs='?', default=None,
        help="Book digits ID that you want to download. You can find it in the URL (X-es):"
             " `" + SAFARI_BASE_URL + "/library/view/book-name/XXXXXXXXXXXXX/`"
    )

    args_parsed = arguments.parse_args()

    # --scan-library: no auth needed, scan and exit
    if args_parsed.scan_library:
        import os as _os
        from library import BookRegistry
        books_dir = _os.path.join(PATH, "Books")
        db_path = _os.path.join(books_dir, "library.db")
        if not _os.path.isdir(books_dir):
            print("Books/ directory not found. Nothing to scan.")
            sys.exit(0)
        reg = BookRegistry(db_path)
        n = reg.scan_existing_books(books_dir)
        reg.close()
        print("Scan complete. %d book(s) added to library.db." % n)
        sys.exit(0)

    if args_parsed.bookid is None:
        arguments.error("the following arguments are required: <BOOK ID>")

    if args_parsed.login:
        email = input("Email: ").strip()
        password = getpass.getpass("Password: ")
        args_parsed.cred = [email, password]

    elif args_parsed.cred:
        args_parsed.cred = SafariBooks.parse_cred(args_parsed.cred)
        if not args_parsed.cred:
            arguments.error(
                "invalid --cred value; expected format: \"account@mail.com:password\""
            )

    # --skip-if-downloaded: check registry before auth/network
    if args_parsed.skip_if_downloaded:
        import os as _os
        from library import BookRegistry
        books_dir = _os.path.join(PATH, "Books")
        db_path = _os.path.join(books_dir, "library.db")
        if _os.path.isdir(books_dir):
            reg = BookRegistry(db_path)
            if reg.is_downloaded(args_parsed.bookid):
                title = reg.get_title(args_parsed.bookid)
                epub = reg.get_epub_path(args_parsed.bookid)
                reg.close()
                print("Book already downloaded: %s" % (title or args_parsed.bookid))
                print("EPUB: %s" % epub)
                sys.exit(0)
            reg.close()

    SafariBooks(args_parsed)
    sys.exit(0)
