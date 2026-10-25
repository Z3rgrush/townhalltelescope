"""Each map region keeps its own source list, data folder and page. Colorado is the only one."""

REGIONS = {
    "co": {"sources": "collector/sources.yaml", "data": "data", "docs": "docs", "state_fips": "08"},
}
