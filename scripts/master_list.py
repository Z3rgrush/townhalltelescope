"""Build the master list of every Colorado county and municipality, with its website and meeting platform.

Starts from data/places.csv and data/zip_areas.csv (scripts/geography.py), takes each government's
official website from Wikidata, then visits each site's home page (and, when needed, the one or
two pages it links as agendas, minutes or meetings) to find where meeting records are posted and
which platform serves them. One request at a time per site, with the project user agent; pages
behind a bot check are recorded as such, never worked around. Writes:
  governments.csv  id, name, kind, county, population, website, meetings_url, platform, collector, status
  governments.md   counts by platform and how many need only configuration

Usage: python scripts/master_list.py [--only lyons,erie]
"""
import argparse
import csv
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from collector.regions import REGIONS  # noqa: E402
from build_map import CITY_COUNTIES, county_source, place_source  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (compatible; townhalltelescope/0.1; +https://github.com/Z3rgrush/townhalltelescope)"}
WIKIDATA = "https://query.wikidata.org/sparql"

# Platform -> pattern found in a page's links or scripts, in priority order. "collector" names the
# adapter in collector/adapters.py that already reads it, so adding such a government is config only.
PLATFORMS = [
    ("legistar", r"\.legistar\.com|legistar\.granicus", "legistar"),
    ("primegov", r"primegov\.com", "primegov"),
    ("civicclerk", r"civicclerk\.com", "civicclerk"),
    ("escribe", r"escribemeetings\.com", "escribe"),
    ("granicus", r"granicus\.com/(?:ViewPublisher|AgendaViewer|MediaPlayer)", "granicus"),
    ("civicweb", r"civicweb\.net", "civicweb"),
    ("onbase", r"OnBaseAgendaOnline", "onbase"),
    ("agendacenter", r"/AgendaCenter", "agendacenter"),
    ("boarddocs", r"boarddocs\.com", ""),
    ("novusagenda", r"novusagenda\.com", ""),
    ("iqm2", r"iqm2\.com", ""),
    ("municode meetings", r"municodemeetings\.com|meetings\.municode\.com", ""),
    ("destiny", r"destinyhosted\.com", ""),
    ("laserfiche", r"/WebLink/|laserfiche", ""),
    ("swagit", r"swagit\.com", ""),
    ("cablecast", r"cablecast", "cablecast"),
]
MEETING_LINK = re.compile(r"agenda|minutes|meetings?\b|council|board of trustees|commissioners|public notices", re.I)
DOC_LINK = re.compile(r"(?:agenda|minutes|packet)[^\"']*\.pdf|\.pdf[^\"']*(?:agenda|minutes)", re.I)
BOT_CHECK = re.compile(r"cf-chl|challenge-platform|captcha|Just a moment\.\.\.|Access denied", re.I)


def sparql(query):
    r = requests.get(WIKIDATA, params={"query": query, "format": "json"}, headers=UA, timeout=120)
    r.raise_for_status()
    return r.json()["results"]["bindings"]


def wikidata_sites():
    """FIPS code -> official website, for Colorado places (7-digit 08xxxxx) and counties (08xxx)."""
    sites = {}
    rows = sparql("""SELECT ?fips ?site WHERE {
      { ?item wdt:P774 ?fips } UNION { ?item wdt:P882 ?fips }
      FILTER(STRSTARTS(?fips, "08"))
      ?item wdt:P856 ?site }""")
    for b in rows:
        fips, site = b["fips"]["value"].replace("-", ""), b["site"]["value"]
        sites.setdefault(fips, site)
    return sites


def wikidata_sites_by_name():
    """English label -> official website, for anything located in Colorado (fallback when FIPS is missing)."""
    rows = sparql("""SELECT ?label ?site WHERE {
      ?item wdt:P131+ wd:Q1261; wdt:P856 ?site; rdfs:label ?label. FILTER(LANG(?label) = "en") }""")
    out = {}
    for b in rows:
        label = re.sub(r",\s*Colorado$", "", b["label"]["value"])
        out.setdefault(label, b["site"]["value"])
    return out


def load_governments(data):
    people = defaultdict(int)
    for r in csv.DictReader((data / "zip_areas.csv").open()):
        people[r["county"]] += int(r["population"])
    county_fips = {}
    govs = []
    for r in csv.DictReader((data / "places.csv").open()):
        kind = "city and county" if r["place"] in CITY_COUNTIES else r["kind"]
        govs.append({"id": place_source(r["place"]), "name": r["place"], "kind": kind, "county": r["county"],
                     "population": int(r["population"]), "fips": r["geoid"]})
        county_fips.setdefault(r["county"], r["geoid"][:2])
    for county, pop in sorted(people.items()):
        if county in CITY_COUNTIES:
            continue
        govs.append({"id": county_source(county), "name": f"{county} County", "kind": "county", "county": county,
                     "population": pop, "fips": ""})
    return govs


def county_fips_codes():
    """County name -> 5-digit FIPS, from the Census county outlines already in the repo."""
    rows = sparql("""SELECT ?fips ?label WHERE {
      ?item wdt:P882 ?fips. FILTER(STRSTARTS(?fips, "08"))
      ?item rdfs:label ?label. FILTER(LANG(?label) = "en") }""")
    return {re.sub(r"\s+County$", "", b["label"]["value"]): b["fips"]["value"] for b in rows}


def get(url):
    try:
        r = requests.get(url, headers=UA, timeout=25)
        return r, None
    except requests.RequestException as e:
        return None, type(e).__name__


def detect(html):
    for name, pattern, collector in PLATFORMS:
        m = re.search(r"[\"'(]([^\"'()\s]*(?:%s)[^\"'()\s]*)" % pattern, html, re.I)
        if m:
            return name, collector, m.group(1)
    return None


def survey(gov):
    """Visit the website and fill in meetings_url, platform, collector and status."""
    site = gov["website"]
    if not site:
        return {**gov, "status": "no website found"}
    r, err = get(site)
    if r is None:
        return {**gov, "status": f"home page failed ({err})"}
    if r.status_code >= 400 or BOT_CHECK.search(r.text[:5000]) and r.status_code in (403, 429, 503):
        return {**gov, "status": f"home page refused ({r.status_code})"}
    found = detect(r.text)
    if found:
        name, collector, url = found
        return {**gov, "meetings_url": urljoin(r.url, url), "platform": name, "collector": collector, "status": "found"}
    host = urlparse(r.url).netloc
    links = []
    for href, text in re.findall(r'href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', r.text, re.S | re.I):
        text = re.sub(r"<[^>]+>|\s+", " ", text).strip()
        url = urljoin(r.url, href.strip())
        if MEETING_LINK.search(text) and url.startswith("http") and url not in links:
            links.append(url)
    # Same-site meeting pages first; an off-site link is usually the platform itself.
    links.sort(key=lambda u: urlparse(u).netloc == host)
    for url in links[:3]:
        time.sleep(1)
        page, _ = get(url)
        if page is None or page.status_code >= 400:
            continue
        found = detect(page.url + " " + page.text)
        if found:
            name, collector, purl = found
            return {**gov, "meetings_url": urljoin(page.url, purl) if purl.startswith(("/", "http")) else page.url,
                    "platform": name, "collector": collector, "status": "found"}
        if DOC_LINK.search(page.text):
            return {**gov, "meetings_url": page.url, "platform": "own site (PDF links)", "collector": "linkscan",
                    "status": "found"}
    if DOC_LINK.search(r.text):
        return {**gov, "meetings_url": r.url, "platform": "own site (PDF links)", "collector": "linkscan", "status": "found"}
    return {**gov, "meetings_url": links[0] if links else "", "status": "no meeting records found"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="co", choices=REGIONS)
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    region = REGIONS[args.region]
    data = ROOT / region["data"]
    govs = load_governments(data)
    tracked = {s["id"] for s in yaml.safe_load((ROOT / region["sources"]).open())}
    sites = wikidata_sites()
    cfips = county_fips_codes()
    by_name = wikidata_sites_by_name()
    for g in govs:
        key = g["fips"] or cfips.get(g["county"], "")
        g["website"] = sites.get(key) or (sites.get(key[2:]) if len(key) == 7 else None) or by_name.get(g["name"], "")
        g.update(meetings_url="", platform="", collector="", status="")
    print(f"{len(govs)} governments, {sum(1 for g in govs if g['website'])} with a website on Wikidata")
    only = {s for s in args.only.split(",") if s}
    todo = [g for g in govs if not only or g["id"] in only]
    with ThreadPoolExecutor(max_workers=8) as pool:  # different sites in parallel, one request at a time each
        done = {g["id"]: g for g in pool.map(survey, todo)}
    rows = [done.get(g["id"], g) for g in govs]
    for g in rows:
        if g["id"] in tracked:
            g["status"] = "collected"
    rows.sort(key=lambda g: -g["population"])
    fields = ["id", "name", "kind", "county", "population", "website", "meetings_url", "platform", "collector", "status"]
    with open(data / "governments.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    write_report(data / "governments.md", rows)


def write_report(path, rows):
    big = [g for g in rows if g["kind"] == "county" or g["population"] >= 2000]
    plat = Counter(g["platform"] or "none found" for g in rows)
    status = Counter(g["status"] for g in rows)
    ready = [g for g in rows if g["collector"] and g["status"] == "found"]
    lines = [
        "# Colorado governments",
        "",
        f"{len(rows)} governments: {sum(g['kind'] == 'county' for g in rows)} counties and "
        f"{sum(g['kind'] != 'county' for g in rows)} cities and towns (Denver and Broomfield count once).",
        "",
        f"- Collected already: {status['collected']}",
        f"- Meeting records found on a platform the collector already reads (config only): {len(ready)}"
        f" ({sum(1 for g in ready if g in big)} of them counties or towns over 2,000 people)",
        f"- Records found on a platform that needs a new adapter: "
        f"{sum(1 for g in rows if g['status'] == 'found' and not g['collector'])}",
        f"- No meeting records found: {status['no meeting records found']}",
        f"- Site refused or failed: {sum(v for k, v in status.items() if k.startswith('home page'))}",
        f"- No website found: {status['no website found']}",
        "",
        "| Platform | Governments | Collector |",
        "|---|---|---|",
    ]
    adapters = {name: c for name, _, c in PLATFORMS}
    adapters["own site (PDF links)"] = "linkscan"
    for name, n in plat.most_common():
        lines.append(f"| {name} | {n} | {adapters.get(name) or ('new adapter' if name != 'none found' else '')} |")
    path.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
