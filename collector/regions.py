"""Each map region keeps its own source list, data folder and page."""

REGIONS = {
    "ks": {"sources": "collector/sources.yaml", "data": "data", "docs": "docs", "state_fips": "20"},
    "co": {"sources": "collector/sources_co.yaml", "data": "data/co", "docs": "docs/co", "state_fips": "08"},
}
