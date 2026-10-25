"""Build the master list of every Colorado county and municipality, with its website and meeting platform.

Starts from data/places.csv and data/zip_areas.csv (scripts/geography.py). Websites come from the
Colorado Municipal League member directory (towns), data/county_websites.csv (counties, copied from
the Colorado Counties Inc. directory) and Wikidata. When those fail, the usual Colorado address
patterns are tried (townofx.colorado.gov, xcounty.gov, ...), keeping one whose page names the
government. Each government is then checked two ways:
  - the public APIs of the meeting platforms the collector reads (Legistar, CivicClerk), asked
    whether a tenant named after the government exists and has meetings in the last 90 days
  - its website: the home page and up to four pages it links as agendas, minutes or meetings,
    looking for a known platform or dated agenda and minutes PDFs
One request at a time per site, with the project user agent. A site that refuses or puts up a bot
check is recorded as such and left alone. Writes:
  governments.csv  one row per government: website, meetings_url, platform, collector, config, status
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
from datetime import date, timedelta
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
CML = "https://cml.org/utility-pages/cml-member-directory"

# Platform -> pattern found in a page's links or scripts, in priority order, and the adapter in
# collector/adapters.py that already reads it (so adding such a government is config only).
PLATFORMS = [
    ("legistar", r"[\w-]+\.legistar\.com", "legistar"),
    ("primegov", r"[\w-]+\.primegov\.com", "primegov"),
    ("civicclerk", r"[\w-]+\.(?:portal|api)\.civicclerk\.com", "civicclerk"),
    ("escribe", r"[\w-]+\.escribemeetings\.com", "escribe"),
    ("granicus", r"[\w-]+\.granicus\.com/(?:ViewPublisher|AgendaViewer|MediaPlayer)[^\"'\s<>]*", "granicus"),
    ("civicweb", r"[\w-]+\.civicweb\.net", "civicweb"),
    ("onbase", r"[^\"'\s<>]*OnBaseAgendaOnline", "onbase"),
    ("agendacenter", r"[^\"'\s<>]*/AgendaCenter", "agendacenter"),
    ("boarddocs", r"go\.boarddocs\.com/[^\"'\s<>]+", ""),
    ("novusagenda", r"[\w-]+\.novusagenda\.com", ""),
    ("iqm2", r"[\w-]+\.iqm2\.com", ""),
    ("municode meetings", r"[\w-]+\.municodemeetings\.com|meetings\.municode\.com[^\"'\s<>]*", ""),
    ("destiny", r"[^\"'\s<>]*destinyhosted\.com[^\"'\s<>]*", ""),
    ("laserfiche", r"[^\"'\s<>]*/WebLink/[^\"'\s<>]*", ""),
    ("swagit", r"[\w-]+\.swagit\.com", ""),
    ("cablecast", r"[^\"'\s<>]*cablecast[^\"'\s<>]*", "cablecast"),
]
MEETING_TEXT = re.compile(r"agendas?|minutes|meetings?\b|packets?", re.I)
COUNCIL_TEXT = re.compile(r"council|board of trustees|trustees|commissioners|public notices|government", re.I)
DOC_LINK = re.compile(r"""href=["'][^"']*(?:agenda|minutes|packet)[^"']*\.pdf""", re.I)
BOT_CHECK = re.compile(r"<title>\s*(?:Just a moment|Attention Required|Access denied)", re.I)


def get(url, timeout=25, **kw):
    try:
        return requests.get(url, headers=UA, timeout=timeout, **kw), None
    except requests.RequestException as e:
        return None, type(e).__name__


def norm(name):
    return re.sub(r"[^a-z]", "", name.lower().replace("&", "and"))


# ---- websites -------------------------------------------------------------------------------

def cml_sites():
    """Municipality name -> website, from the CML member directory."""
    r, _ = get(CML)
    out = {}
    if r is None or r.status_code != 200:
        print("CML directory unavailable")
        return out
    for href, text in re.findall(r'<a[^>]+href=["\'](https?://[^"\']+)["\'][^>]*>(.*?)</a>', r.text, re.S | re.I):
        name = re.sub(r"<[^>]+>|\*|\s+", " ", text).strip()
        if name and "cml.org" not in href:
            out.setdefault(norm(name), href)
    print(f"CML directory: {len(out)} links")
    return out


def county_sites(data):
    return {r["county"]: r["website"] for r in csv.DictReader((data / "county_websites.csv").open())}


def guesses(gov):
    """Common Colorado website addresses for a government, most likely first."""
    x = norm(gov["name"].removesuffix(" County"))
    if gov["kind"] == "county":
        return [f"https://www.{x}county.gov", f"https://{x}county.colorado.gov", f"https://www.{x}countyco.gov",
                f"https://www.{x}county.org", f"https://www.co.{x}.co.us", f"https://www.{x}county.us",
                f"https://www.{x}county.com", f"https://www.{x}countyco.com"]
    return [f"https://{x}.colorado.gov", f"https://townof{x}.colorado.gov", f"https://cityof{x}.colorado.gov",
            f"https://www.{x}co.gov", f"https://www.townof{x}.com", f"https://www.townof{x}.org",
            f"https://www.cityof{x}.com", f"https://www.cityof{x}.org", f"https://www.{x}gov.com",
            f"https://www.{x}.org", f"https://www.{x}colorado.gov"]


def sparql(query):
    r = requests.get(WIKIDATA, params={"query": query, "format": "json"}, headers=UA, timeout=120)
    r.raise_for_status()
    return r.json()["results"]["bindings"]


def wikidata_sites():
    """Normalized name ("boulder", "bouldercounty") -> official website, for places in Colorado."""
    rows = sparql("""SELECT ?label ?site WHERE {
      ?item wdt:P131+ wd:Q1261; wdt:P856 ?site; rdfs:label ?label. FILTER(LANG(?label) = "en") }""")
    out = {}
    for b in rows:
        out.setdefault(norm(re.sub(r",\s*Colorado$", "", b["label"]["value"])), b["site"]["value"])
    print(f"Wikidata: {len(out)} sites")
    return out


# ---- governments ----------------------------------------------------------------------------

def load_governments(data):
    people = defaultdict(int)
    for r in csv.DictReader((data / "zip_areas.csv").open()):
        people[r["county"]] += int(r["population"])
    govs = []
    for r in csv.DictReader((data / "places.csv").open()):
        kind = "city and county" if r["place"] in CITY_COUNTIES else r["kind"]
        govs.append({"id": place_source(r["place"]), "name": r["place"], "kind": kind, "county": r["county"],
                     "population": int(r["population"])})
    for county, pop in sorted(people.items()):
        if county not in CITY_COUNTIES:
            govs.append({"id": county_source(county), "name": f"{county} County", "kind": "county",
                         "county": county, "population": pop})
    return govs


# ---- platform checks ------------------------------------------------------------------------

def tenant_names(gov):
    base = norm(gov["name"].removesuffix(" County"))
    if gov["kind"] == "county":
        return [base + "county", base + "countyco"]
    return [base + "co", base]


def json_of(r):
    try:
        return r.json() if r is not None and r.status_code == 200 else None
    except ValueError:
        return None


def probe_apis(gov):
    """Ask Legistar and CivicClerk directly whether this government has a tenant with recent meetings."""
    since = (date.today() - timedelta(days=90)).isoformat()
    for name in tenant_names(gov):
        events = json_of(get(f"https://webapi.legistar.com/v1/{name}/events",
                             params={"$filter": f"EventDate ge datetime'{since}'", "$top": "200"}, timeout=10)[0])
        if isinstance(events, list) and events:
            return {"platform": "legistar", "collector": "legistar", "config": f"client={name}",
                    "meetings_url": f"https://{name}.legistar.com/Calendar.aspx", "recent": len(events)}
        events = json_of(get(f"https://{name}.api.civicclerk.com/v1/Events",
                             params={"$filter": f"startDateTime ge {since}T00:00:00Z", "$top": "200"}, timeout=10)[0])
        if isinstance(events, dict) and events.get("value"):
            return {"platform": "civicclerk", "collector": "civicclerk", "config": f"tenant={name}",
                    "meetings_url": f"https://{name}.portal.civicclerk.com",
                    "recent": f"{len(events['value'])}{'+' if events.get('@odata.nextLink') else ''}"}
        time.sleep(0.5)
    return None


def config_for(platform, url):
    host = urlparse(url).netloc
    sub = host.split(".")[0]
    if platform == "legistar":
        return f"client={sub}"
    if platform == "civicclerk":
        return f"tenant={sub}"
    if platform == "primegov":
        return f"tenant={sub}"
    if platform in ("escribe", "civicweb"):
        return f"host={host}"
    if platform == "granicus":
        m = re.search(r"view_id=(\d+)", url)
        return f"host={host}" + (f" view_id={m.group(1)}" if m else "")
    if platform == "agendacenter":
        return f"url={url.split('/AgendaCenter')[0]}/AgendaCenter"
    return ""


def detect(page_url, html):
    for name, pattern, collector in PLATFORMS:
        m = re.search(pattern, page_url + " " + html, re.I)
        if m:
            found = m.group(0)
            url = found if found.startswith("http") else urljoin(page_url, found if found.startswith("/") else "//" + found)
            return {"platform": name, "collector": collector, "meetings_url": url, "config": config_for(name, url)}
    return None


def links_from(page_url, html):
    meeting, council = [], []
    for href, text in re.findall(r'href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', html, re.S | re.I):
        text = re.sub(r"<[^>]+>|\s+", " ", text).strip()
        url = urljoin(page_url, href.strip())
        if not url.startswith("http") or url.lower().endswith(".pdf"):
            continue
        if MEETING_TEXT.search(text) and url not in meeting:
            meeting.append(url)
        elif COUNCIL_TEXT.search(text) and url not in council:
            council.append(url)
    return meeting + council


def open_site(gov):
    """The first of the government's known or likely websites that answers and names it."""
    first_error = None
    sites = [s for s in [gov["website"], *gov.get("more_sites", [])] if s]
    for i, url in enumerate(sites + guesses(gov)):
        r, err = get(url, timeout=15)
        guessed = i >= len(sites)
        if r is None:
            first_error = first_error or f"site failed ({err})"
            continue
        if r.status_code >= 400 or BOT_CHECK.search(r.text[:5000]):
            if not guessed:
                first_error = first_error or f"site refused ({r.status_code})"
            continue
        name = gov["name"].removesuffix(" County").lower()
        if guessed and name not in r.text[:200000].lower():
            continue
        return r, None
    return None, first_error or "no website found"


def survey_site(gov):
    r, err = open_site(gov)
    if r is None:
        return {"status": err}
    gov["website"] = r.url
    found = detect(r.url, r.text)
    if found:
        return {**found, "status": "found"}
    host = urlparse(r.url).netloc.removeprefix("www.")
    pages = links_from(r.url, r.text)
    tried, fallback = 0, ""
    for url in pages:
        if tried >= 4:
            break
        same_site = urlparse(url).netloc.removeprefix("www.").endswith(host) or host.endswith(
            urlparse(url).netloc.removeprefix("www."))
        found = detect(url, "")
        if found and found["collector"] != "cablecast":
            return {**found, "status": "found"}
        if not same_site:
            continue
        tried += 1
        time.sleep(1)
        page, _ = get(url)
        if page is None or page.status_code >= 400:
            continue
        found = detect(page.url, page.text)
        if found:
            return {**found, "status": "found"}
        if DOC_LINK.search(page.text):
            return {"platform": "own site (PDF links)", "collector": "linkscan", "meetings_url": page.url,
                    "config": f"url={page.url}", "status": "found"}
        fallback = fallback or page.url
    if DOC_LINK.search(r.text):
        return {"platform": "own site (PDF links)", "collector": "linkscan", "meetings_url": r.url,
                "config": f"url={r.url}", "status": "found"}
    return {"meetings_url": fallback, "status": "no meeting records found"}


def survey(gov):
    gov = dict(gov)
    found = probe_apis(gov)
    if found:
        out = {**gov, **found, "status": "found"}
    else:
        out = {**gov, **survey_site(gov)}
    print(f"{out['name']}: {out['status']} {out.get('platform', '')}", flush=True)
    return out


# ---- main -----------------------------------------------------------------------------------

FIELDS = ["id", "name", "kind", "county", "population", "website", "meetings_url", "platform", "collector",
          "config", "recent", "status"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="co", choices=REGIONS)
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    region = REGIONS[args.region]
    data = ROOT / region["data"]
    govs = load_governments(data)
    tracked = {s["id"]: s for s in yaml.safe_load((ROOT / region["sources"]).open())}
    towns, counties, wiki = cml_sites(), county_sites(data), wikidata_sites()
    for g in govs:
        n = norm(g["name"])
        site = counties.get(g["county"]) if g["kind"] == "county" else towns.get(n)
        g["website"] = site or wiki.get(n) or ""
        g["more_sites"] = [wiki[n]] if site and wiki.get(n) and wiki[n] != site else []
        g.update({k: "" for k in FIELDS if k not in g})
    print(f"{len(govs)} governments, {sum(1 for g in govs if g['website'])} with a website")
    only = {s for s in args.only.split(",") if s}
    todo = [g for g in govs if not only or g["id"] in only]
    with ThreadPoolExecutor(max_workers=8) as pool:  # different sites in parallel, one request at a time each
        done = {g["id"]: g for g in pool.map(survey, todo)}
    rows = [done.get(g["id"], g) for g in govs]
    for g in rows:
        if g["id"] in tracked:
            g["status"] = "collected"
            g["collector"] = g["collector"] or tracked[g["id"]]["adapter"]
    rows.sort(key=lambda g: -g["population"])
    with open(data / "governments.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    write_report(data / "governments.md", rows)


def write_report(path, rows):
    big = {g["id"] for g in rows if g["kind"] == "county" or g["population"] >= 2000}
    status = Counter(g["status"] for g in rows)
    ready = [g for g in rows if g["collector"] and g["status"] == "found"]
    new = [g for g in rows if g["status"] == "found" and not g["collector"]]
    people = sum(g["population"] for g in rows if g["kind"] != "county")
    lines = [
        "# Colorado governments",
        "",
        f"{len(rows)} governments: {sum(g['kind'] == 'county' for g in rows)} counties and "
        f"{sum(g['kind'] != 'county' for g in rows)} cities and towns (Denver and Broomfield count once). "
        f"{sum(1 for g in rows if g['id'] in big)} are counties or towns over 2,000 people.",
        "",
        "| | All | Counties and towns over 2,000 |",
        "|---|---|---|",
    ]
    def row(label, items):
        items = list(items)
        lines.append(f"| {label} | {len(items)} | {sum(1 for g in items if g['id'] in big)} |")
    row("Collected already", (g for g in rows if g["status"] == "collected"))
    row("Records on a platform the collector reads (config only)", ready)
    row("Records on a platform that needs a new adapter", new)
    row("Website found, no meeting records found", (g for g in rows if g["status"] == "no meeting records found"))
    row("Site refused or failed", (g for g in rows if g["status"].startswith("site")))
    row("No website found", (g for g in rows if g["status"] == "no website found"))
    covered = sum(g["population"] for g in rows if g["kind"] != "county" and g["status"] in ("collected", "found")
                  and (g["collector"] or g["status"] == "collected"))
    lines += ["", f"Towns and cities that are collected or config only hold {covered / people:.0%} of the "
              "people living in incorporated places.", "", "| Platform | Governments | Collector |", "|---|---|---|"]
    adapters = {name: c for name, _, c in PLATFORMS}
    adapters["own site (PDF links)"] = "linkscan"
    for name, n in Counter(g["platform"] or "none found" for g in rows).most_common():
        lines.append(f"| {name} | {n} | {adapters.get(name) or ('new adapter' if name != 'none found' else '')} |")
    path.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
