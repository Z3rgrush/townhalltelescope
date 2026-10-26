"""One function per meeting platform. Each yields Doc records for meetings between start and end."""
import json
import re
from datetime import date, datetime, timedelta
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

soup_text = lambda html: BeautifulSoup(html, "html.parser")

from .common import Doc, find_date, get, kind_of, session, soup

KEYWORDS = re.compile(r"minute|agenda|packet|summary|journal", re.I)
DOCLIKE = re.compile(
    r"\.pdf|\.docx?|AgendaViewer|MinutesViewer|showpublisheddocument|showdocument|DocumentCenter/View|ViewFile|/uploads/|/document/\d+|/files/|mccmeetings|Archive\.aspx\?ADID=|GetFile\.ashx",
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
        elif "MeetingInformation.aspx?Id=" in href:
            # The list shows each body's three most recent meetings. Newer portals (Summit County) head each
            # body with its latest meeting instead of a type link, so the body is the title before the date.
            name = body or text.split(" - ")[0]
            d = find_date(text)
            if bodies.search(name) and _in_window(d, start, end):
                meetings[href] = (name, text, d)
    # Some portals (Douglas County) file agendas and minutes in top-level folders not tied to a body.
    folders += [("County Commission", urljoin(base + "/", f)) for f in src.get("folders", [])]
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


def destiny(src, start, end, log):
    """Destiny Hosted Agendas (AgendaQuick): one listing per month, each meeting's agenda an HTML page."""
    base = src["base"]
    months, m = [], date(start.year, start.month, 1)
    while m <= end:
        months.append(m)
        m = date(m.year + m.month // 12, m.month % 12 + 1, 1)
    seen, n = set(), 0
    for m in months:
        if "agenda_publish.cfm" in base:
            url = f"{base}&mt=ALL&get_month={m.month}&get_year={m.year}"
        else:
            url = f"{base.rstrip('/')}/default.cfm?mt=ALL&month={m.month}&year={m.year}"
        page, final = soup(url)
        for a in page.find_all("a", href=True):
            href = urljoin(final, a["href"])
            if not re.search(r"dsp=ag&|agenda\.cfm\?seq=", href) or href in seen:
                continue
            seen.add(href)
            label = a.get("title") or a.get_text(" ", strip=True)
            row = a.find_parent("tr")
            d = find_date(label, row.get_text(" ", strip=True) if row else "")
            if not _in_window(d, start, end):
                continue
            body = re.search(r"for (.+?) \(", label)
            body = body.group(1) if body else (row.find_all("td")[1].get_text(" ", strip=True) if row and len(row.find_all("td")) > 1 else "Meeting")
            n += 1
            yield Doc(src["id"], body, d, f"{body} - Agenda", href, "agenda")
    log(f"  {n} agendas in window")


def agendasuite(src, start, end, log):
    """AgendaSuite (El Paso County): the meeting list's past 90 days, each meeting's details page holding its agenda."""
    base = src["base"].rstrip("/")
    bodies = re.compile(src.get("bodies", "."), re.I)
    seen, n = set(), 0
    for i in range(0, 8):
        # The list pages past meetings, newest first; stop once a page adds nothing in the window.
        page, final = soup(f"{base}/meeting/list" + (f"/{i}" if i else ""))
        before = n
        for doc in _agendasuite_rows(src["id"], page, final, bodies, start, end, seen):
            n += 1
            yield doc
        if i and n == before:
            break
    log(f"  {n} meetings in window")


def _agendasuite_rows(source, page, final, bodies, start, end, seen):
    for tr in page.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
        link = tr.find("a", href=re.compile(r"/meeting/details/\d+"))
        if len(cells) < 5 or not link:
            continue
        d, body = find_date(cells[2]), cells[4]
        href = urljoin(final, link["href"])
        if href in seen or not bodies.search(body) or not _in_window(d, start, end):
            continue
        seen.add(href)
        yield Doc(source, body, d, f"{body} - {cells[1]}", href, "agenda")


def weblink(src, start, end, log):
    """Meeting pages that link their agenda and minutes in a Laserfiche WebLink repository (Weld County).

    WebLink's viewer is a script, but it reads each page's text from the repository's public document
    service, so we ask that service for the text the same way."""
    follow = re.compile(src["follow"])
    meetings = {}
    for page_url in src["pages"]:
        page, final = soup(page_url)
        for a in page.find_all("a", href=True):
            href = urljoin(final, a["href"])
            d = find_date(a.get("title", ""), a.get_text(" ", strip=True), href)
            if follow.search(href) and _in_window(d, start, end):
                meetings.setdefault(href, d)
    log(f"  {len(meetings)} meeting pages in window")
    seen = set()
    for murl, d in meetings.items():
        page, final = soup(murl)
        for a in page.find_all("a", href=True):
            m = re.search(r"(https?://[^/]+/WebLink)/DocView\.aspx\?id=(\d+).*?repo=(\w+)", urljoin(final, a["href"]))
            if not m or m.group(2) in seen:
                continue
            seen.add(m.group(2))
            service, doc_id, repo = m.group(1) + "/DocumentService.aspx", int(m.group(2)), m.group(3)
            info = session.post(f"{service}/GetBasicDocumentInfo", json={"repoName": repo, "entryId": doc_id}, timeout=60)
            if not info.ok:
                continue
            pages = []
            for n in range(1, min(info.json()["data"].get("pageCount") or 0, 40) + 1):
                r = session.post(f"{service}/GetTextHtmlForPage", timeout=60, json={
                    "repoName": repo, "documentId": doc_id, "pageNum": n, "showAnn": False, "searchUuid": ""})
                if r.ok:
                    pages.append(r.json()["data"].get("text") or "")
            text = re.sub(r"[ \t]+", " ", "\n".join(pages)).strip()
            if text:
                title = a.get_text(" ", strip=True) or "Agenda"
                yield Doc(src["id"], src.get("body", "Board of County Commissioners"), d, f"{d:%B %-d, %Y} - {title}",
                          f"{m.group(1)}/DocView.aspx?id={doc_id}&dbid=0&repo={repo}", kind_of(title), {"text": text})


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
            # Some sites (Manhattan) put a dated "Posted ..." heading on each row; the body is the
            # nearest heading without a date.
            heading = next((h for h in a.find_all_previous(["h2", "h3"]) if not find_date(h.get_text(" ", strip=True))), None)
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
                        # Detail pages often list other meetings' files too, so trust a date in the file itself.
                        d2 = find_date(t2, h2) or d
                        if _in_window(d2, start, end):
                            yield Doc(src["id"], "City Council", d2, f"{text} - {t2}", h2, kind_of(t2 + " " + h2))


def legistar(src, start, end, log):
    """Legistar's public Web API (Denver). Each event links one agenda and, once approved, minutes."""
    api = f"https://webapi.legistar.com/v1/{src['client']}/events"
    bodies = re.compile(src["bodies"], re.I) if src.get("bodies") else None
    flt = f"EventDate ge datetime'{start.isoformat()}' and EventDate le datetime'{end.isoformat()}'"
    skip, n = 0, 0
    while True:
        r = get(api, params={"$filter": flt, "$orderby": "EventDate", "$top": 500, "$skip": skip})
        r.raise_for_status()
        events = r.json()
        for ev in events:
            n += 1
            body = ev.get("EventBodyName") or "Meeting"
            if bodies and not bodies.search(body):
                continue
            url = ev.get("EventMinutesFile") or ev.get("EventAgendaFile")
            if not url:
                continue
            kind = "minutes" if ev.get("EventMinutesFile") else "agenda"
            d = date.fromisoformat(ev["EventDate"][:10])
            yield Doc(src["id"], body, d, f"{body} {kind}", url, kind, {"public_url": ev.get("EventInSiteURL")})
        if len(events) < 500:
            break
        skip += 500
    log(f"  {n} events in window")


def primegov(src, start, end, log):
    """PrimeGov public portals (Boulder, Longmont) list meetings and their compiled documents as JSON."""
    base = f"https://{src['tenant']}.primegov.com"
    bodies = re.compile(src["bodies"], re.I) if src.get("bodies") else None
    meetings = []
    for year in range(start.year, end.year + 1):
        r = get(f"{base}/api/v2/PublicPortal/ListArchivedMeetings", params={"year": year})
        r.raise_for_status()
        meetings += r.json()
    n = 0
    for m in meetings:
        d = date.fromisoformat(m["dateTime"][:10])
        if not start <= d <= end or re.search(r"cancel", m.get("title", ""), re.I):
            continue
        body = re.split(r"\s+[-–]\s+", m.get("title") or "Meeting")[0].strip()
        if bodies and not bodies.search(body):
            continue
        n += 1
        docs = [x for x in m.get("documentList") or [] if re.search(r"minute|agenda", x.get("templateName", ""), re.I)
                and not re.search(r"packet", x.get("templateName", ""), re.I)]
        # Some portals (Westminster) publish only packets; a packet then stands in for the agenda.
        docs = docs or [x for x in m.get("documentList") or [] if re.search(r"packet", x.get("templateName", ""), re.I)]
        minutes = [x for x in docs if re.search(r"minute", x["templateName"], re.I)]
        # compileOutputType 1 is a PDF; 3 is an HTML agenda, which only renders on the portal page.
        for x in sorted(minutes or docs, key=lambda x: x.get("compileOutputType") != 1)[:1]:
            if x.get("compileOutputType") == 3:
                url = f"{base}/Portal/Meeting?meetingTemplateId={x['templateId']}"
            else:
                url = f"{base}/Public/CompiledDocument?meetingTemplateId={x['templateId']}&compileOutputType={x['compileOutputType']}"
            yield Doc(src["id"], body, d, f"{m.get('title')} - {x['templateName']}", url, kind_of(x["templateName"]),
                      {"public_url": f"{base}/Portal/Meeting?meetingTemplateId={x['templateId']}"})
    log(f"  {n} meetings in window")


def escribe(src, start, end, log):
    """eScribe portals (Boulder County) answer a calendar query with each meeting's documents."""
    base = src["base"].rstrip("/")
    bodies = re.compile(src["bodies"], re.I) if src.get("bodies") else None
    r = session.post(f"{base}/MeetingsCalendarView.aspx/GetCalendarMeetings", timeout=60,
                     json={"calendarStartDate": start.isoformat(), "calendarEndDate": end.isoformat()})
    r.raise_for_status()
    meetings = r.json().get("d") or []
    if meetings:
        log(f"  document types: {sorted({x.get('Type') for m in meetings for x in m.get('MeetingDocumentLink') or []})}")
    n = 0
    for m in meetings:
        body = m.get("MeetingName") or m.get("MeetingType") or "Meeting"
        if bodies and not bodies.search(body):
            continue
        try:
            d = date.fromisoformat(str(m.get("StartDate", ""))[:10].replace("/", "-"))
        except ValueError:
            d = find_date(m.get("FormattedStart"))
        if not _in_window(d, start, end):
            continue
        n += 1
        links = m.get("MeetingDocumentLink") or []
        if src.get("html"):
            # Some portals put their files behind a browser check; the agenda and minutes pages themselves are plain HTML.
            pages = [x for x in links if "Meeting.aspx" in str(x.get("Url"))]
            minutes = [x for x in pages if re.search(r"minute", str(x.get("Url")) + str(x.get("Type")), re.I)]
            for x in (minutes or pages)[:1]:
                yield Doc(src["id"], body, d, f"{body} - {x.get('Title') or x.get('Type') or 'Agenda'}",
                          urljoin(base + "/", x["Url"]), "minutes" if minutes else "agenda")
            continue
        pdfs = [x for x in links if "pdf" in str(x.get("Format", "")).lower() and KEYWORDS.search(str(x.get("Type", "")))
                and not re.search(r"cover", str(x.get("Type", "")), re.I)]
        minutes = [x for x in pdfs if re.search(r"minute", str(x.get("Type", "")) + str(x.get("Title", "")), re.I)]
        for x in (minutes or pdfs)[:1]:
            yield Doc(src["id"], body, d, f"{body} - {x.get('Title') or x.get('Type')}", urljoin(base + "/", x["Url"]),
                      "minutes" if minutes else "agenda", {"public_url": f"{base}/Meeting.aspx?Id={m.get('ID')}&Agenda=Agenda&lang=English"})
    log(f"  {len(meetings)} meetings returned, {n} kept")


def cablecast(src, start, end, log):
    """Cablecast video archives (Broomfield council): each meeting video's chapters are its agenda item titles."""
    api = src["base"].rstrip("/") + "/cablecastapi/v1"
    titles = re.compile(src.get("titles", "."), re.I)
    offset, n, older = 0, 0, 0
    # Shows come newest event first; stop after a full page that is entirely before the window.
    while older < 20 and offset < 600:
        r = get(f"{api}/shows", params={"search": src.get("search", ""), "page_size": 20, "offset": offset})
        r.raise_for_status()
        shows = r.json().get("shows") or []
        if not shows:
            break
        offset += len(shows)
        for sh in shows:
            d = date.fromisoformat(sh["eventDate"][:10])
            if d < start:
                older += 1
                continue
            older = 0
            if d > end or not titles.search(sh["title"]):
                continue
            body = re.split(r"\s+-\s+", sh["title"])[0].strip()
            if src.get("agenda_file"):
                # Some stations (Salina) attach the agenda PDF to each show instead of chaptering it.
                n += 1
                url = f"{src['base'].rstrip('/')}/cablecastapi/shows/{sh['id']}/{src['agenda_file']}"
                yield Doc(src["id"], re.sub(r"\s+\w+ \d{1,2}, \d{4}$", "", body), d, sh["title"], url, "agenda")
                continue
            if not sh.get("vods"):
                continue
            vtt = get(f"{api}/vods/{sh['vods'][0]}/chapters")
            if not vtt.ok:
                continue
            items = [ln.strip() for ln in vtt.text.splitlines()
                     if ln.strip() and not re.fullmatch(r"\d+|WEBVTT|[\d:.]+ --> [\d:.]+", ln.strip())]
            if not items:
                continue
            n += 1
            url = f"{api}/vods/{sh['vods'][0]}/chapters"
            text = f"{sh['title']}\nAgenda items (from the meeting video's chapters):\n" + "\n".join(items)
            yield Doc(src["id"], body, d, sh["title"], url, "agenda", {"text": text})
    log(f"  {n} meeting videos with chapters in window")


def wp_events(src, start, end, log):
    """WordPress "The Events Calendar" JSON (Boulder County): public meeting and hearing descriptions."""
    api = src["base"].rstrip("/") + "/wp-json/tribe/events/v1/events"
    titles = re.compile(src.get("titles", "."), re.I)
    cut = re.compile(r"Attend (?:In-Person|Virtually|or Participate|the Public Hearing)|More Information|Accessibility Access", re.I)
    page, n = 1, 0
    while True:
        r = get(api, params={"categories": src["categories"], "start_date": start.isoformat(),
                             "end_date": end.isoformat(), "per_page": 50, "page": page})
        if r.status_code == 400 or not r.ok:  # past the last page
            break
        data = r.json()
        for ev in data.get("events") or []:
            title = BeautifulSoup(ev["title"], "html.parser").get_text()
            if not titles.search(title):
                continue
            d = date.fromisoformat(ev["start_date"][:10])
            desc = BeautifulSoup(ev.get("description") or "", "html.parser").get_text("\n", strip=True)
            desc = cut.split(desc)[0].strip()
            if len(desc) < 200:
                continue
            n += 1
            body = re.sub(r"\s*\((?:Hybrid|Virtual)\)|^Public (?:Meeting|Hearing):\s*", "", title).strip()
            yield Doc(src["id"], body, d, title, ev["url"], "agenda",
                      {"text": f"{title}\n{desc}"})
        if page >= int(data.get("total_pages") or 1):
            break
        page += 1
    log(f"  {n} events with descriptions in window")


def _granicus_s3(url):
    """AgendaViewer redirects to an S3 bucket whose name has underscores, which fails TLS checks on the
    virtual-host address; the same file is served from the path-style address."""
    r = session.get(url, allow_redirects=False, timeout=60)
    loc = r.headers.get("Location", "")
    m = re.match(r"https?://([\w-]+)\.s3\.amazonaws\.com/(.*)", loc)
    if m:
        return f"https://s3.amazonaws.com/{m.group(1)}/{m.group(2)}"
    # Sedgwick County's viewers forward to an OnBase meeting page that loads its text by script;
    # ask OnBase for that text directly.
    m = re.match(r"(.*/OnBaseAgendaOnline)/Meetings/ViewMeeting\?id=(\d+)&doctype=(\d)", loc)
    if m:
        kind = {"1": "agenda", "2": "minutes", "3": "summary"}[m.group(3)]
        return f"{m.group(1)}/Documents/ViewAgenda?meetingId={m.group(2)}&type={kind}&doctype={m.group(3)}"
    return urljoin(url, loc) if loc else url


def granicus(src, start, end, log):
    """Granicus ViewPublisher pages (Topeka): one table row per meeting with agenda and minutes links."""
    base = src["base"].rstrip("/")
    page, _ = soup(f"{base}/ViewPublisher.php?view_id={src.get('view_id', 1)}")
    n = 0
    for row in page.find_all("tr"):
        cells = [re.sub(r"\s*/\s*", "/", td.get_text(" ", strip=True).replace("\xa0", " ")) for td in row.find_all("td")]
        d = find_date(" ".join(cells))
        if not cells or not _in_window(d, start, end) or re.search(r"cancel", cells[0], re.I):
            continue
        hrefs = [urljoin(base + "/", a["href"]) for a in row.find_all("a", href=True)]
        minutes = [h for h in hrefs if "/minutes/" in h or "MinutesViewer" in h]
        if src.get("resolve_minutes"):
            minutes = [_granicus_s3(h) for h in minutes]
        agenda = [_granicus_s3(h) for h in hrefs if "AgendaViewer" in h]
        for h, kind in ([(minutes[0], "minutes")] if minutes else []) + ([(agenda[0], "agenda")] if agenda else []):
            n += 1
            yield Doc(src["id"], cells[0], d, f"{cells[0]} {d}", h, kind)
    log(f"  {n} documents in window")


def onbase(src, start, end, log):
    """Hyland OnBase Agenda Online (Johnson County). DownloadFile serves a script page that redirects to
    DownloadFileBytes, so we link the bytes URL directly. "Summary" files are agendas annotated with actions."""
    base = src["base"].rstrip("/")
    home, _ = soup(f"{base}/")
    pages = [home]
    form, _ = soup(f"{base}/Meetings")
    tok = form.find("input", attrs={"name": "__RequestVerificationToken"})
    if tok:
        fmt = lambda d: f"{d.month}/{d.day}/{d.year}"
        r = session.post(f"{base}/Meetings", timeout=60, data={
            "__RequestVerificationToken": tok.get("value", ""), "Keywords": "",
            "MeetingTypeIDs": ",".join(src.get("meeting_types", [])), "DateRangeOptionID": "11",
            "DateRangeCustomStartDate": fmt(start), "DateRangeCustomEndDate": fmt(end)})
        if r.ok:
            found = soup_text(r.text)
            pages.append(found)
            # Results may list meetings rather than files; open each meeting page for its documents.
            views = {urljoin(r.url, a["href"]) for a in found.find_all("a", href=True)
                     if re.search(r"ViewMeeting|MeetingDetail", a["href"], re.I)}
            log(f"  search: {len(found.find_all('a', href=True))} links, {len(views)} meeting pages")
            for v in sorted(views):
                pages.append(soup(v)[0])
        else:
            log(f"  search returned HTTP {r.status_code}; using the home page list only")
    seen, n = set(), 0
    for pg in pages:
        for a in pg.find_all("a", href=True):
            m = re.search(r"DownloadFile/((.+?)_\d+_(Agenda|Summary)_(\d{1,2})_(\d{1,2})_(20\d\d)_[^?]*)\?(.*)", a["href"])
            if not m:
                continue
            name, body, doctype, mo, dy, yr, qs = m.groups()
            d = date(int(yr), int(mo), int(dy))
            if name in seen or not _in_window(d, start, end):
                continue
            seen.add(name)
            n += 1
            body = body.replace("_", " ")
            url = f"{base}/Documents/DownloadFileBytes/{name}?{qs}"
            yield Doc(src["id"], body, d, f"{body} {doctype} {d}", url, "minutes" if doctype == "Summary" else "agenda")
    log(f"  {n} documents in window from {len(pages)} page(s)")


ADAPTERS = {f.__name__: f for f in (civicweb, destiny, agendasuite, weblink, civicclerk, agendacenter, linkscan, legistar, primegov, escribe,
                                    cablecast, wp_events, granicus, onbase)}
