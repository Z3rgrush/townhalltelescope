"""One function per meeting platform. Each yields Doc records for meetings between start and end."""
import re
from datetime import date, datetime, timedelta
from urllib.parse import urljoin, urlparse

from .common import Doc, find_date, get, kind_of, soup

KEYWORDS = re.compile(r"minute|agenda|packet|summary|journal", re.I)
DOCLIKE = re.compile(
    r"\.pdf|\.docx?|AgendaViewer|MinutesViewer|showpublisheddocument|showdocument|DocumentCenter/View|ViewFile|/uploads/|/document/\d+|/files/|mccmeetings",
    re.I,
)


def _in_window(d, start, end):
    return d is not None and start <= d <= end


def civicweb(src, start, end, log):
    """iCompass CivicWeb portals (Overland Park, Leawood, Shawnee)."""
    base = src["base"].rstrip("/")
    bodies = re.compile(src.get("bodies", "."), re.I)
    page, _ = soup(f"{base}/Portal/MeetingTypeList.aspx")
    body, folders, meetings = None, [], {}
    for a in page.find_all("a", href=True):
        href, text = urljoin(base + "/Portal/", a["href"]), a.get_text(" ", strip=True)
        if "MeetingInformation.aspx?type=" in href:
            body = text
        elif body and "/filepro/documents/" in href and re.search(r"agenda|minute|summar", text, re.I):
            folders.append((body, href))
        elif body and "MeetingInformation.aspx?Id=" in href and bodies.search(body):
            # The list shows each body's three most recent meetings.
            d = find_date(text)
            if _in_window(d, start, end):
                meetings[href] = (body, text, d)
    seen = set()

    def emit(body_name, title, url, d):
        m = re.search(r"/document/(\d+)", url)
        key = m.group(1) if m else url
        if key in seen:
            return None
        seen.add(key)
        return Doc(src["id"], body_name, d, title, url, kind_of(title + " " + url))

    # 1. Each meeting's page links its published agenda (and sometimes minutes).
    log(f"  {len(meetings)} recent meetings in window")
    for murl, (body_name, mtitle, d) in meetings.items():
        mpage, _ = soup(murl)
        for a in mpage.find_all("a", href=True):
            href = urljoin(base + "/", a["href"])
            if re.search(r"/document/\d+", href):
                title = a.get_text(" ", strip=True) or requests_name(href) or mtitle
                doc = emit(body_name, f"{mtitle} - {title}", href, d)
                if doc:
                    yield doc

    # 2. Approved minutes are filed in per-year folders in the document center.
    years = {str(y) for y in range(start.year, end.year + 1)}
    for body_name, furl in folders:
        if not bodies.search(body_name):
            continue
        for doc_url, title in _civicweb_folder(base, furl, years, depth=0):
            d = find_date(title, doc_url)
            if _in_window(d, start, end):
                doc = emit(body_name, title, doc_url, d)
                if doc:
                    yield doc


def requests_name(url):
    path = urlparse(url).path.rsplit("/", 1)[-1]
    return re.sub(r"%20|_", " ", path)


def _civicweb_folder(base, url, years, depth):
    page, _ = soup(url)
    for a in page.find_all("a", href=True):
        href, text = urljoin(base + "/", a["href"]), a.get_text(" ", strip=True)
        if re.search(r"/document/\d+", href):
            yield href, text or requests_name(href)
        elif depth < 2 and re.search(r"/filepro/documents/\d+/?$", href) and (
            text in years or re.match(rf"({'|'.join(['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec'])})", text, re.I)
        ):
            yield from _civicweb_folder(base, href, years, depth + 1)


def civicclerk(src, start, end, log):
    """CivicClerk portals expose a public OData API (Unified Government, Bonner Springs)."""
    api = f"https://{src['tenant']}.api.civicclerk.com/v1"
    portal = f"https://{src['tenant']}.portal.civicclerk.com"
    flt = f"startDateTime ge {start.isoformat()}T00:00:00Z and startDateTime le {end.isoformat()}T23:59:59Z"
    url = f"{api}/Events"
    params = {"$filter": flt, "$orderby": "startDateTime"}
    n = 0
    while url:
        r = get(url, params=params)
        r.raise_for_status()
        data = r.json()
        for ev in data.get("value", []):
            n += 1
            d = datetime.fromisoformat(ev["startDateTime"].replace("Z", "+00:00")).date()
            body = ev.get("categoryName") or ev.get("eventName") or "Meeting"
            for f in ev.get("publishedFiles", []) or []:
                ftype = (f.get("type") or "") + " " + (f.get("name") or "")
                if not KEYWORDS.search(ftype):
                    continue
                fid = f.get("fileId")
                yield Doc(
                    src["id"], body, d, f"{ev.get('eventName')} - {f.get('name') or f.get('type')}",
                    f"{api}/Meetings/GetMeetingFileStream(fileId={fid},plainText=false)",
                    kind_of(ftype),
                    {"public_url": f"{portal}/event/{ev['id']}/files"},
                )
        url, params = data.get("@odata.nextLink"), None
    log(f"  {n} events in window")


def agendacenter(src, start, end, log):
    """CivicPlus AgendaCenter pages; file links carry the date as _MMDDYYYY-ID."""
    base = src["base"].rstrip("/")
    seen = set()
    search = f"{base}/AgendaCenter/Search/?term=&CIDs=all&startDate={start:%m/%d/%Y}&endDate={end:%m/%d/%Y}&dateRange=&dateSelector="
    found = 0
    for page_url in (search, f"{base}/AgendaCenter"):
        if found:
            break
        page, _ = soup(page_url)
        for a in page.find_all("a", href=True):
            href = urljoin(base + "/", a["href"]).split("?")[0]
            m = re.search(r"/AgendaCenter/ViewFile/(Agenda|Minutes)/_(\d{8})-(\d+)", href)
            if not m or href in seen:
                continue
            seen.add(href)
            d = find_date(m.group(2))
            if not _in_window(d, start, end):
                continue
            found += 1
            heading = a.find_previous(["h2", "h3"])
            body = heading.get_text(" ", strip=True) if heading else "City Council"
            row = a.find_parent("tr")
            title = row.get_text(" ", strip=True)[:160] if row else a.get_text(" ", strip=True)
            yield Doc(src["id"], body, d, title, href, "minutes" if m.group(1) == "Minutes" else "agenda")
        log(f"  {page_url}: {found} files in window")


def linkscan(src, start, end, log):
    """Fallback for custom city sites: scan listing pages for dated agenda/minutes links."""
    seen = set()
    follow = re.compile(src["follow"]) if src.get("follow") else None
    for page_url in src["pages"]:
        try:
            page, final = soup(page_url)
        except Exception as e:
            log(f"  skip {page_url}: {e}")
            continue
        host = urlparse(final).netloc
        candidates = []
        for a in page.find_all("a", href=True):
            href, text = urljoin(final, a["href"]), a.get_text(" ", strip=True)
            row = a.find_parent(["tr", "li"])
            context = row.get_text(" ", strip=True)[:200] if row else ""
            if not (KEYWORDS.search(text) or KEYWORDS.search(href) or (follow and follow.search(href))):
                continue
            d = find_date(text, href, context)
            if _in_window(d, start, end):
                candidates.append((href, text, d))
        log(f"  {page_url}: {len(candidates)} dated links in window")
        for href, text, d in candidates:
            if href in seen:
                continue
            seen.add(href)
            if DOCLIKE.search(href):
                yield Doc(src["id"], "City Council", d, text or href, href, kind_of(text + " " + href))
            elif urlparse(href).netloc == host:
                # A meeting detail page: take the documents linked from it.
                try:
                    sub, sub_final = soup(href)
                except Exception:
                    continue
                for a in sub.find_all("a", href=True):
                    h2, t2 = urljoin(sub_final, a["href"]), a.get_text(" ", strip=True)
                    if DOCLIKE.search(h2) and (KEYWORDS.search(t2) or KEYWORDS.search(h2)) and h2 not in seen:
                        seen.add(h2)
                        yield Doc(src["id"], "City Council", d, f"{text} - {t2}", h2, kind_of(t2 + " " + h2))


ADAPTERS = {f.__name__: f for f in (civicweb, civicclerk, agendacenter, linkscan)}
