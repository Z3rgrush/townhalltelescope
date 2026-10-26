"""Fetch each source's listing page and print the links on it, to design scrapers against."""
import csv, re, sys
from urllib.parse import urljoin
import requests

UA = {"User-Agent": "Mozilla/5.0 (compatible; townhalltelescope/0.1; +https://github.com/Z3rgrush/townhalltelescope)"}
KEY = re.compile(r"minute|agenda|packet|meeting|document|viewfile|getfile|event|portal|\.pdf", re.I)

# A URL prefixed with "raw:" prints the start of the response body instead of its links; "full:" prints up to 60 KB.
urls = sys.argv[1:]
if not urls:
    urls = [r["minutes_url"] for r in csv.DictReader(open("data/sources.csv")) if r["minutes_url"]]
def escribe(base):
    """Ask an eScribe portal for last month's meetings, then fetch one meeting's HTML agenda page and one file."""
    from datetime import date, timedelta
    r = requests.post(f"{base}/MeetingsCalendarView.aspx/GetCalendarMeetings", headers=UA, timeout=30,
                      json={"calendarStartDate": (date.today() - timedelta(days=40)).isoformat(),
                            "calendarEndDate": date.today().isoformat()})
    print("calendar", r.status_code, len(r.text))
    meetings = r.json().get("d") or [] if r.ok else []
    for m in meetings[:3]:
        print(" ", m.get("ID"), m.get("MeetingName"), m.get("StartDate"), [x.get("Url") for x in m.get("MeetingDocumentLink") or []][:3])
    for m in meetings[:2]:
        for page in (f"{base}/Meeting.aspx?Id={m['ID']}&Agenda=Agenda&lang=English",
                     *[urljoin(base + "/", x["Url"]) for x in (m.get("MeetingDocumentLink") or [])[:1]]):
            p = requests.get(page, headers=UA, timeout=30)
            body = re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", p.text)))
            print("   ", p.status_code, p.headers.get("content-type"), len(p.text), page, "|", body[:400] if "html" in str(p.headers.get("content-type")) else "")


for url in urls:
    if url.startswith("escribe:"):
        print(f"\n===== {url}")
        try:
            escribe(url.removeprefix("escribe:").rstrip("/"))
        except Exception as e:
            print("ERROR", e)
        continue
    text = url.startswith("text:")
    url = url.removeprefix("text:")
    full = url.startswith("full:")
    raw = full or url.startswith("raw:")
    url = url.removeprefix("raw:").removeprefix("full:")
    print(f"\n===== {url}")
    try:
        r = requests.get(url, headers=UA, timeout=30)
    except Exception as e:
        print("ERROR", e); continue
    print("status", r.status_code, "type", r.headers.get("content-type"), "len", len(r.text), "final", r.url)
    print("server", {k: v for k, v in r.headers.items() if k.lower() in (
        "server", "via", "x-cache", "cf-ray", "x-akamai-transformed", "x-iinfo", "x-cdn", "x-powered-by", "set-cookie")})
    if text:
        body = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", r.text)
        body = re.sub(r"(?s)<[^>]+>", "\n", body)
        print(re.sub(r"\s*\n\s*", "\n", body)[:6000])
        hrefs = re.findall(r'href=["\']([^"\'#]+)', r.text)
        print("...LINKS:", *[h for h in hrefs if KEY.search(h) or not h.endswith((".css", ".js", ".png", ".ico", ".svg"))][:200], sep="\n")
        continue
    if raw:
        print(r.text[:60000 if full else 3000])
        hits = re.findall(r".{0,120}(?:AMID|ADID|ViewFile|api/|apiUrl|baseUrl|MeetingID|meeting/).{0,160}", r.text)
        print("...MATCHES:", *hits[:60], sep="\n")
        continue
    links = re.findall(r'href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', r.text, re.S | re.I)
    seen = set()
    for href, text in links:
        full = urljoin(r.url, href.strip())
        text = re.sub(r"<[^>]+>|\s+", " ", text).strip()[:80]
        if full in seen or not (KEY.search(full) or KEY.search(text)):
            continue
        seen.add(full)
        print(f"  {text!r:60} {full}")
        if len(seen) >= 150:
            print("  ..."); break
