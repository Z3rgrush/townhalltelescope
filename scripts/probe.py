"""Fetch each source's listing page and print the links on it, to design scrapers against."""
import csv, re, sys
from urllib.parse import urljoin
import requests

UA = {"User-Agent": "Mozilla/5.0 (compatible; townhalltelescope/0.1; +https://github.com/Z3rgrush/townhalltelescope)"}
KEY = re.compile(r"minute|agenda|packet|meeting|document|viewfile|event|portal|\.pdf", re.I)

# A URL prefixed with "raw:" prints the start of the response body instead of its links.
urls = sys.argv[1:]
if not urls:
    urls = [r["minutes_url"] for r in csv.DictReader(open("data/sources.csv")) if r["minutes_url"]]
for url in urls:
    raw = url.startswith("raw:")
    url = url.removeprefix("raw:")
    print(f"\n===== {url}")
    try:
        r = requests.get(url, headers=UA, timeout=30)
    except Exception as e:
        print("ERROR", e); continue
    print("status", r.status_code, "type", r.headers.get("content-type"), "len", len(r.text))
    if raw:
        print(r.text[:3000])
        hits = re.findall(r".{0,120}(?:AMID|ADID|ViewFile|api/|apiUrl|baseUrl).{0,160}", r.text)
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
