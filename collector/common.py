"""HTTP, date parsing and text extraction shared by the adapters."""
import io
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

UA = "Mozilla/5.0 (compatible; townhalltelescope/0.1; +https://github.com/Z3rgrush/townhalltelescope)"
session = requests.Session()
session.headers["User-Agent"] = UA

MAX_BYTES = 40_000_000
MAX_PAGES = 60  # agenda packets can run to hundreds of pages; the first ones carry the agenda itself


@dataclass
class Doc:
    source: str
    body: str
    date: date
    title: str
    url: str
    kind: str  # "minutes" or "agenda"
    extra: dict = field(default_factory=dict)


def get(url, **kw):
    last = None
    for attempt in range(3):
        try:
            r = session.get(url, timeout=kw.pop("timeout", 45), **kw)
            if r.status_code < 500:
                time.sleep(0.5)  # be polite to small city servers
                return r
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = str(e)
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{url}: {last}")


def soup(url):
    r = get(url)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser"), r.url


def kind_of(text):
    return "minutes" if re.search(r"minute|summary|journal", text, re.I) else "agenda"


MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
_PATTERNS = [
    (re.compile(r"(20\d\d)[-_.](\d{1,2})[-_.](\d{1,2})"), "ymd"),
    (re.compile(r"(?<!\d)(\d{1,2})[-_./](\d{1,2})[-_./](20\d\d)(?!\d)"), "mdy"),
    (re.compile(r"(?<!\d)(\d{2})(\d{2})(20\d\d)(?!\d)"), "mdy"),
    (re.compile(rf"\b({MONTHS})[a-z]*\.?[\s_-]+(\d{{1,2}})(?:st|nd|rd|th)?,?[\s_-]+(20\d\d)", re.I), "Mdy"),
    (re.compile(rf"\b(\d{{1,2}})[\s_-]+({MONTHS})[a-z]*\.?,?[\s_-]+(20\d\d)", re.I), "dMy"),
    (re.compile(r"(?<!\d)(\d{1,2})[-_.](\d{1,2})[-_.](\d{2})(?!\d)"), "mdyy"),
]


def _month(name):
    return ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"].index(name[:3].lower()) + 1


def find_date(*texts):
    """First plausible meeting date found in any of the texts (titles, file names, URLs)."""
    for text in texts:
        if not text:
            continue
        text = requests.utils.unquote(text)
        for rx, order in _PATTERNS:
            for m in rx.finditer(text):
                g = m.groups()
                try:
                    if order == "ymd":
                        d = date(int(g[0]), int(g[1]), int(g[2]))
                    elif order == "mdy":
                        d = date(int(g[2]), int(g[0]), int(g[1]))
                    elif order == "Mdy":
                        d = date(int(g[2]), _month(g[0]), int(g[1]))
                    elif order == "dMy":
                        d = date(int(g[2]), _month(g[1]), int(g[0]))
                    else:
                        d = date(2000 + int(g[2]), int(g[0]), int(g[1]))
                except ValueError:
                    continue
                if 2015 <= d.year <= datetime.now().year + 1:
                    return d
    return None


def extract_text(url):
    """Download a document and return its plain text ("" if it isn't readable)."""
    r = get(url, stream=True)
    r.raise_for_status()
    data = r.raw.read(MAX_BYTES + 1, decode_content=True)
    if len(data) > MAX_BYTES:
        return "", "too large"
    ctype = r.headers.get("content-type", "")
    if data[:4] == b"%PDF" or "pdf" in ctype:
        try:
            reader = PdfReader(io.BytesIO(data))
            pages = reader.pages[:MAX_PAGES]
            text = "\n".join((p.extract_text() or "") for p in pages)
        except Exception as e:  # malformed PDFs are common on small city sites
            return "", f"pdf error: {e}"
        if len(text.strip()) < 200:
            return text, "scanned pdf (no text layer)"
        return text, "ok"
    if "html" in ctype or data.lstrip()[:1] == b"<":
        s = BeautifulSoup(data, "html.parser")
        for t in s(["script", "style", "nav", "header", "footer"]):
            t.decompose()
        return s.get_text("\n", strip=True), "ok"
    return "", f"unsupported type {ctype}"
