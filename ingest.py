"""BioVector data ingestion pipeline.

Fetches real *Aedes aegypti* occurrence records from GBIF and historical
temperature and precipitation data from the Open-Meteo archive, then writes a
reproducible analysis snapshot to the local ``data`` directory.
"""

from __future__ import annotations

import argparse
import calendar
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

SPECIES_NAME = "Aedes aegypti"
AEDES_AEGYPTI_TAXON_KEY = 1651891
GBIF_API = "https://api.gbif.org/v1"
OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
GBIF_SOURCE = "https://www.gbif.org"
OPEN_METEO_SOURCE = "https://open-meteo.com"
PIPELINE_VERSION = "1.0.0"

DEFAULT_MAX_RECORDS = 12_000
DEFAULT_START_YEAR = 2000
DEFAULT_ZONE_COUNT = 6
DEFAULT_GRID_DEGREES = 5.0
GBIF_PAGE_SIZE = 300
GBIF_MAX_WORKERS = 4
SAMPLE_COUNTRY_LIMIT = 50
SAMPLE_COUNTRY_FLOOR = 30

OCCURRENCE_COLUMNS = [
    "gbifID",
    "scientificName",
    "decimalLatitude",
    "decimalLongitude",
    "countryCode",
    "country",
    "eventDate",
    "datePrecision",
    "basisOfRecord",
    "year",
    "month",
]

ZONE_COLUMNS = [
    "zone_id",
    "zone_label",
    "latitude",
    "longitude",
    "sample_occurrences",
    "share_pct",
    "dominant_country",
    "grid_cell",
]

COUNTRY_COLUMNS = [
    "countryCode",
    "country",
    "gbif_records",
    "sample_records",
    "sample_fraction_pct",
]

MONTHLY_COLUMNS = ["month", "month_name", "sample_records", "share_pct"]

BASIS_COLUMNS = ["basisOfRecord", "gbif_records", "share_pct"]


_THREAD_STATE = threading.local()


def _build_session() -> requests.Session:
    """Create an HTTP session with retry handling for transient API failures."""
    session = requests.Session()
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        status=5,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(
        {
            "User-Agent": (
                "BioVector/1.0 (quantitative biodiversity informatics dashboard)"
            ),
            "Accept": "application/json",
        }
    )
    return session


def _thread_session() -> requests.Session:
    session = getattr(_THREAD_STATE, "session", None)
    if session is None:
        session = _build_session()
        _THREAD_STATE.session = session
    return session


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _get_api_payload(
    url: str,
    params: dict[str, Any],
    timeout: int = 45,
) -> Any:
    """Fetch JSON from an API and raise a useful error when the call fails."""
    response = _thread_session().get(url, params=params, timeout=timeout)
    response.raise_for_status()
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(f"Invalid JSON returned by {url}") from exc


def _get_json(
    url: str,
    params: dict[str, Any],
    timeout: int = 45,
) -> dict[str, Any]:
    payload = _get_api_payload(url, params=params, timeout=timeout)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected API payload returned by {url}")
    return payload


def resolve_species_taxon() -> dict[str, Any]:
    """Resolve and verify the accepted GBIF backbone key for Aedes aegypti (taxonKey=1651891)."""
    match = _get_json(
        f"{GBIF_API}/species/match",
        {"name": SPECIES_NAME, "verbose": "false"},
    )
    if match.get("matchType") == "NONE" or not match.get("usageKey"):
        taxon_key = AEDES_AEGYPTI_TAXON_KEY
        detail = _get_json(f"{GBIF_API}/species/{taxon_key}", {})
    else:
        taxon_key = int(match["usageKey"])
        detail = _get_json(f"{GBIF_API}/species/{taxon_key}", {})

    accepted_name = detail.get("canonicalName") or detail.get("scientificName") or SPECIES_NAME
    if accepted_name.casefold() != SPECIES_NAME.casefold():
        raise RuntimeError(
            "GBIF name resolution returned an unexpected taxon: "
            f"{detail.get('scientificName')!r}."
        )

    return {
        "scientific_name": detail.get("scientificName", SPECIES_NAME),
        "canonical_name": accepted_name,
        "taxon_key": AEDES_AEGYPTI_TAXON_KEY,
        "rank": detail.get("rank"),
        "status": detail.get("taxonomicStatus"),
        "kingdom": detail.get("kingdom"),
        "phylum": detail.get("phylum"),
        "class": detail.get("class"),
        "order": detail.get("order"),
        "family": detail.get("family"),
        "genus": detail.get("genus"),
        "identifier_note": (
            "BioVector targets Aedes aegypti using the accepted GBIF Taxonomic Key 1651891. "
            "This ensures precise taxonomic filtering and guards against species misidentification, "
            "preventing data from being mixed with sibling vector taxa (such as Aedes albopictus)."
        ),
    }


def _clean_occurrence_records(raw_records: list[dict[str, Any]]) -> pd.DataFrame:
    """Normalize GBIF JSON fields and remove records without usable coordinates or dates."""
    rows = []
    for record in raw_records:
        rows.append(
            {
                "gbifID": record.get("key"),
                "scientificName": record.get("scientificName"),
                "decimalLatitude": record.get("decimalLatitude"),
                "decimalLongitude": record.get("decimalLongitude"),
                "countryCode": record.get("countryCode"),
                "country": record.get("country"),
                "eventDate": record.get("eventDate"),
                "basisOfRecord": record.get("basisOfRecord"),
            }
        )

    raw_columns = [
        "gbifID",
        "scientificName",
        "decimalLatitude",
        "decimalLongitude",
        "countryCode",
        "country",
        "eventDate",
        "basisOfRecord",
    ]
    frame = pd.DataFrame(rows, columns=raw_columns)
    if frame.empty:
        return pd.DataFrame(columns=OCCURRENCE_COLUMNS)

    frame["decimalLatitude"] = pd.to_numeric(
        frame["decimalLatitude"], errors="coerce"
    )
    frame["decimalLongitude"] = pd.to_numeric(
        frame["decimalLongitude"], errors="coerce"
    )
    frame = frame[
        frame["decimalLatitude"].between(-90, 90)
        & frame["decimalLongitude"].between(-180, 180)
    ].copy()

    date_text = (
        frame["eventDate"]
        .astype("string")
        .str.strip()
        .str.split("/")
        .str[0]
        .str.replace(r"Z$", "", regex=True)
    )
    date_precision = np.select(
        [
            date_text.str.match(r"^\d{4}-\d{2}-\d{2}", na=False).fillna(False),
            date_text.str.match(r"^\d{4}-\d{2}$", na=False).fillna(False),
            date_text.str.match(r"^\d{4}$", na=False).fillna(False),
        ],
        ["day", "month", "year"],
        default="unknown",
    )
    frame["eventDate"] = pd.to_datetime(
        date_text, errors="coerce", utc=True, format="mixed"
    )
    frame["datePrecision"] = date_precision
    frame = frame.dropna(
        subset=["decimalLatitude", "decimalLongitude", "eventDate"]
    ).copy()

    frame["countryCode"] = (
        frame["countryCode"].astype("string").str.strip().str.upper()
    )
    frame["country"] = frame["country"].astype("string").str.strip()
    frame["country"] = frame["country"].fillna("").replace("", pd.NA)
    frame["country"] = frame["country"].fillna(
        frame["countryCode"].fillna("Unassigned")
    )

    frame["basisOfRecord"] = (
        frame["basisOfRecord"]
        .astype("string")
        .str.strip()
        .str.upper()
        .str.replace("_", " ", regex=False)
        .str.title()
        .fillna("Unknown")
    )
    frame["scientificName"] = (
        frame["scientificName"].astype("string").fillna(SPECIES_NAME)
    )
    frame["gbifID"] = frame["gbifID"].astype("string")

    frame["year"] = frame["eventDate"].dt.year.astype("Int64")
    month_known = frame["datePrecision"].isin(["day", "month"])
    frame["month"] = (
        frame["eventDate"].dt.month.where(month_known).astype("Int64")
    )

    frame = frame.drop_duplicates(subset=["gbifID", "eventDate", "decimalLatitude"])
    frame = frame.sort_values(
        ["eventDate", "gbifID"], ascending=[False, False]
    ).reset_index(drop=True)

    return frame[OCCURRENCE_COLUMNS]


def _facet_counts(payload: dict[str, Any], field: str) -> list[dict[str, Any]]:
    """Extract one facet from a GBIF search response."""
    for facet in payload.get("facets", []):
        if str(facet.get("field", "")).upper() == field.upper():
            counts = facet.get("counts", [])
            if isinstance(counts, list):
                return counts
    return []


def _country_names() -> dict[str, str]:
    """Fetch the official ISO-3166 country labels exposed by GBIF."""
    try:
        payload = _get_api_payload(f"{GBIF_API}/enumeration/country", {})
    except (requests.RequestException, RuntimeError):
        return {}
    if not isinstance(payload, list):
        return {}
    names: dict[str, str] = {}
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        code = str(entry.get("iso2", "")).strip().upper()
        title = str(entry.get("title", "")).strip()
        if code and title:
            names[code] = title
    return names


def _build_country_totals(
    payload: dict[str, Any],
) -> pd.DataFrame:
    """Convert source-side GBIF country facets into an analysis-ready table."""
    counts = _facet_counts(payload, "COUNTRY")
    names = _country_names()
    rows = []
    for item in counts:
        code = str(item.get("name", "")).strip().upper()
        if not code:
            continue
        rows.append(
            {
                "countryCode": code,
                "country": names.get(code, code),
                "gbif_records": int(item.get("count", 0)),
            }
        )
    frame = pd.DataFrame(rows, columns=COUNTRY_COLUMNS[:3])
    if frame.empty:
        raise RuntimeError("GBIF did not return a country facet for the query.")
    frame = frame.sort_values(
        ["gbif_records", "countryCode"], ascending=[False, True]
    ).reset_index(drop=True)
    frame["sample_records"] = 0
    frame["sample_fraction_pct"] = 0.0
    return frame


def _build_monthly_profile(occurrences: pd.DataFrame) -> pd.DataFrame:
    """Build a month-level seasonal profile from records with known calendar months.

    GBIF's interpreted month facet can omit records whose source dataset supplies
    year-only dates. The profile is therefore derived from cleaned occurrence
    dates, excluding year-only records from month-specific analysis rather than
    assigning them to an artificial January.
    """
    month_records = occurrences[
        occurrences["datePrecision"].isin(["day", "month"])
        & occurrences["month"].notna()
    ]
    if month_records.empty:
        raise RuntimeError(
            "No sampled GBIF records have month-level date precision for seasonality."
        )
    counts = (
        month_records.groupby("month")
        .size()
        .reindex(range(1, 13), fill_value=0)
        .astype(int)
    )
    frame = pd.DataFrame(
        {"month": counts.index.astype(int), "sample_records": counts.to_numpy()}
    )
    total = int(frame["sample_records"].sum())
    frame["month_name"] = frame["month"].map(
        lambda value: calendar.month_name[int(value)]
    )
    frame["share_pct"] = (
        frame["sample_records"] / total * 100.0 if total else 0.0
    ).round(2)
    return frame[MONTHLY_COLUMNS]


def _build_basis_totals(payload: dict[str, Any]) -> pd.DataFrame:
    """Convert GBIF basis-of-record facets into a share table."""
    counts = _facet_counts(payload, "BASIS_OF_RECORD")
    rows = []
    for item in counts:
        label = (
            str(item.get("name", "Unknown"))
            .strip()
            .replace("_", " ")
            .title()
        )
        rows.append(
            {
                "basisOfRecord": label,
                "gbif_records": int(item.get("count", 0)),
            }
        )
    frame = pd.DataFrame(rows, columns=BASIS_COLUMNS[:2])
    if frame.empty:
        raise RuntimeError("GBIF did not return a basis-of-record facet.")
    frame["share_pct"] = (
        frame["gbif_records"] / frame["gbif_records"].sum() * 100.0
    ).round(2)
    return frame.sort_values("gbif_records", ascending=False).reset_index(drop=True)


def _country_sample_plan(
    country_totals: pd.DataFrame,
    max_records: int,
) -> pd.DataFrame:
    """Allocate a broad, proportionally weighted record sample across countries.

    GBIF offsets become expensive deep in a result set. Sampling within the
    highest-contribution countries avoids deep pagination while retaining
    representation from smaller range-edge countries.
    """
    pool = country_totals.head(SAMPLE_COUNTRY_LIMIT).copy()
    if pool.empty:
        raise ValueError("No countries are available for occurrence sampling.")

    floor = min(SAMPLE_COUNTRY_FLOOR, max(0, max_records // len(pool)))
    residual_budget = max(0, max_records - floor * len(pool))
    pool_total = float(pool["gbif_records"].sum())
    proportional = (
        pool["gbif_records"] / pool_total * residual_budget
        if pool_total
        else 0.0
    )
    pool["sample_quota"] = (
        floor + np.floor(proportional.to_numpy(dtype=float))
    ).astype(int)
    pool["sample_quota"] = np.minimum(
        pool["sample_quota"].to_numpy(), pool["gbif_records"].to_numpy()
    )

    return pool[
        ["countryCode", "country", "gbif_records", "sample_quota"]
    ].reset_index(drop=True)


def _fetch_country_pages(
    base_query: dict[str, Any],
    sample_plan: pd.DataFrame,
    verbose: bool,
) -> tuple[list[dict[str, Any]], int]:
    """Fetch country-stratified pages concurrently with bounded API concurrency."""
    jobs: list[dict[str, Any]] = []
    for row in sample_plan.itertuples(index=False):
        remaining = int(row.sample_quota)
        offset = 0
        while remaining > 0:
            page_limit = min(GBIF_PAGE_SIZE, remaining)
            jobs.append(
                {
                    "countryCode": row.countryCode,
                    "offset": offset,
                    "limit": page_limit,
                }
            )
            offset += page_limit
            remaining -= page_limit

    raw_records: list[dict[str, Any]] = []
    completed = 0

    def fetch_job(job: dict[str, Any]) -> list[dict[str, Any]]:
        payload = _get_json(
            f"{GBIF_API}/occurrence/search",
            {
                **base_query,
                "country": job["countryCode"],
                "offset": job["offset"],
                "limit": job["limit"],
            },
        )
        return payload.get("results", [])

    with ThreadPoolExecutor(max_workers=GBIF_MAX_WORKERS) as executor:
        futures = {executor.submit(fetch_job, job): job for job in jobs}
        for future in as_completed(futures):
            records = future.result()
            raw_records.extend(records)
            completed += 1
            if verbose and (
                completed == 1
                or completed == len(futures)
                or completed % 10 == 0
            ):
                print(
                    f"  GBIF sample pages {completed}/{len(futures)} complete; "
                    f"{len(raw_records):,} raw records received"
                )

    return raw_records, len(jobs)


def fetch_occurrences(
    max_records: int = DEFAULT_MAX_RECORDS,
    start_year: int = DEFAULT_START_YEAR,
    end_year: int | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    """Fetch complete-query facets and a country-stratified, cleaned occurrence sample."""
    if max_records < 1:
        raise ValueError("max_records must be a positive integer.")

    taxon = resolve_species_taxon()
    end_year = end_year or date.today().year
    if start_year > end_year:
        raise ValueError("start_year cannot be after end_year.")

    base_query = {
        "taxonKey": AEDES_AEGYPTI_TAXON_KEY,  # 1651891
        "hasCoordinate": "true",
        "hasGeospatialIssue": "false",
        "occurrenceStatus": "PRESENT",
        "year": f"{start_year},{end_year}",
    }

    count_payload = _get_json(
        f"{GBIF_API}/occurrence/search",
        {
            **base_query,
            "facet": ["country", "basisOfRecord"],
            "facetLimit": 300,
            "limit": 0,
        },
    )
    total_available = int(count_payload.get("count", 0))
    if total_available == 0:
        raise RuntimeError(
            f"GBIF returned no georeferenced {SPECIES_NAME} records for the query."
        )

    country_totals = _build_country_totals(count_payload)
    basis_totals = _build_basis_totals(count_payload)

    sample_plan = _country_sample_plan(country_totals, max_records)
    if verbose:
        print(
            f"  GBIF query reports {total_available:,} matching records across "
            f"{len(country_totals):,} countries."
        )
        print(
            f"  Sampling up to {int(sample_plan['sample_quota'].sum()):,} records "
            f"from {len(sample_plan):,} countries..."
        )

    raw_records, request_count = _fetch_country_pages(
        base_query, sample_plan, verbose=verbose
    )
    cleaned = _clean_occurrence_records(raw_records)
    if cleaned.empty:
        raise RuntimeError(
            "No GBIF records remained after coordinate and date cleaning."
        )

    monthly_totals = _build_monthly_profile(cleaned)
    precision_counts = (
        cleaned["datePrecision"].value_counts().astype(int).to_dict()
    )
    month_dated_records = int(monthly_totals["sample_records"].sum())

    sample_counts = cleaned.groupby("countryCode", dropna=False).size()
    country_totals["sample_records"] = (
        country_totals["countryCode"].map(sample_counts).fillna(0).astype(int)
    )
    country_totals["sample_fraction_pct"] = (
        country_totals["sample_records"]
        / country_totals["gbif_records"].replace(0, np.nan)
        * 100.0
    ).fillna(0.0).round(2)

    metadata = {
        "source_name": "GBIF Occurrence API",
        "source_url": "https://api.gbif.org/v1/occurrence/search",
        "homepage": GBIF_SOURCE,
        "retrieved_at_utc": _utc_now_iso(),
        "query_parameters": base_query,
        "query_total": total_available,
        "raw_records_pulled": len(raw_records),
        "valid_records": int(len(cleaned)),
        "records_discarded": int(len(raw_records) - len(cleaned)),
        "unique_countries": int(cleaned["countryCode"].nunique(dropna=True)),
        "sampling": {
            "strategy": (
                "Country-stratified proportional quota with a minimum allocation "
                "for range-edge countries; geographic coordinates and dates are "
                "unmodified from GBIF."
            ),
            "sampled_records_target": int(sample_plan["sample_quota"].sum()),
            "countries_requested": int(len(sample_plan)),
            "countries_available": int(len(country_totals)),
            "api_page_requests": request_count,
        },
        "date_precision_counts": precision_counts,
        "monthly_profile": {
            "records_used": month_dated_records,
            "share_of_sample_pct": round(
                month_dated_records / len(cleaned) * 100.0, 2
            ),
            "note": (
                "Seasonality uses only sampled records with day- or month-level "
                "event dates. Year-only records remain in the map but are excluded "
                "from month-specific analyses."
            ),
        },
        "source_side_facets": {
            "note": (
                "Country and basis-of-record totals are complete GBIF query facets "
                "and are not extrapolated from the coordinate sample. GBIF's "
                "interpreted month facet is not used because it omits records "
                "whose source datasets provide only year-level dates."
            ),
            "country_facet_records": int(country_totals["gbif_records"].sum()),
            "basis_facet_records": int(basis_totals["gbif_records"].sum()),
        },
        "date_span": {
            "start": cleaned["eventDate"].min().date().isoformat(),
            "end": cleaned["eventDate"].max().date().isoformat(),
        },
        "taxon": taxon,
    }
    return {
        "occurrences": cleaned,
        "country_totals": country_totals[COUNTRY_COLUMNS],
        "monthly_totals": monthly_totals,
        "basis_totals": basis_totals,
        "metadata": metadata,
    }


def derive_core_zones(
    occurrences: pd.DataFrame,
    zone_count: int = DEFAULT_ZONE_COUNT,
    grid_degrees: float = DEFAULT_GRID_DEGREES,
) -> pd.DataFrame:
    """Group occurrence coordinates into geographic grid cells and rank the densest zones."""
    if occurrences.empty:
        raise ValueError("Cannot derive core zones from an empty occurrence table.")
    if zone_count < 1:
        raise ValueError("zone_count must be at least 1.")

    frame = occurrences.dropna(
        subset=["decimalLatitude", "decimalLongitude", "country"]
    ).copy()
    lat_cell = np.floor((frame["decimalLatitude"] + 90.0) / grid_degrees).astype(int)
    lon_cell = np.floor((frame["decimalLongitude"] + 180.0) / grid_degrees).astype(int)
    frame["_grid_cell"] = lat_cell.astype(str) + ":" + lon_cell.astype(str)

    grouped = (
        frame.groupby("_grid_cell", as_index=False)
        .agg(
            latitude=("decimalLatitude", "mean"),
            longitude=("decimalLongitude", "mean"),
            sample_occurrences=("decimalLatitude", "size"),
        )
        .sort_values(
            ["sample_occurrences", "_grid_cell"], ascending=[False, True]
        )
        .head(zone_count)
        .reset_index(drop=True)
    )

    dominant_country = (
        frame.groupby(["_grid_cell", "country"])
        .size()
        .rename("country_records")
        .reset_index()
        .sort_values(
            ["_grid_cell", "country_records", "country"],
            ascending=[True, False, True],
        )
        .drop_duplicates("_grid_cell")
        .set_index("_grid_cell")["country"]
    )
    grouped["dominant_country"] = grouped["_grid_cell"].map(dominant_country)
    grouped["dominant_country"] = grouped["dominant_country"].fillna("Unassigned")
    grouped["share_pct"] = (
        grouped["sample_occurrences"] / len(frame) * 100.0
    ).round(2)
    grouped["zone_id"] = [f"Z{index:02d}" for index in range(1, len(grouped) + 1)]
    grouped["zone_label"] = (
        grouped["zone_id"] + " · " + grouped["dominant_country"].astype(str)
    )
    grouped["grid_cell"] = grouped["_grid_cell"]
    grouped["latitude"] = grouped["latitude"].round(5)
    grouped["longitude"] = grouped["longitude"].round(5)

    return grouped[ZONE_COLUMNS]


def last_complete_climate_period(today: date | None = None) -> tuple[date, date]:
    """Return the 12 complete calendar months ending before the current month."""
    current = today or date.today()
    end = current.replace(day=1) - timedelta(days=1)
    month_index = end.year * 12 + (end.month - 1) - 11
    start = date(month_index // 12, month_index % 12 + 1, 1)
    return start, end


def fetch_climate_profiles(
    zones: pd.DataFrame,
    start_date: date,
    end_date: date,
    verbose: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Fetch real historical weather for each core zone and aggregate it monthly.

    Daily temperature means and precipitation totals are requested for every
    core-zone centroid. The monthly table is derived directly from those daily
    observations; no climate values are interpolated or simulated.
    """
    if zones.empty:
        raise ValueError("At least one core zone is required for climate retrieval.")
    if start_date > end_date:
        raise ValueError("start_date cannot be after end_date.")

    monthly_frames: list[pd.DataFrame] = []
    daily_frames: list[pd.DataFrame] = []
    failures: list[dict[str, Any]] = []

    for position, zone in enumerate(zones.itertuples(index=False), start=1):
        params = {
            "latitude": f"{zone.latitude:.5f}",
            "longitude": f"{zone.longitude:.5f}",
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "daily": "temperature_2m_mean,precipitation_sum",
            "timezone": "UTC",
            "temperature_unit": "celsius",
            "precipitation_unit": "mm",
        }
        try:
            payload = _get_json(OPEN_METEO_ARCHIVE, params, timeout=60)
            daily_payload = payload.get("daily")
            if not isinstance(daily_payload, dict) or not daily_payload.get("time"):
                raise RuntimeError("Open-Meteo returned no daily climate series.")

            daily = pd.DataFrame(
                {
                    "date": pd.to_datetime(
                        daily_payload["time"], errors="coerce", utc=True
                    ),
                    "temperature_c": pd.to_numeric(
                        pd.Series(daily_payload.get("temperature_2m_mean", [])),
                        errors="coerce",
                    ),
                    "precipitation_mm": pd.to_numeric(
                        pd.Series(daily_payload.get("precipitation_sum", [])),
                        errors="coerce",
                    ),
                }
            ).dropna(subset=["date"])

            if daily[["temperature_c", "precipitation_mm"]].notna().sum().sum() == 0:
                raise RuntimeError("Open-Meteo returned no usable climate values.")

            daily["zone_id"] = zone.zone_id
            daily["zone_label"] = zone.zone_label
            daily["latitude"] = float(zone.latitude)
            daily["longitude"] = float(zone.longitude)
            daily["temperature_c"] = daily["temperature_c"].round(2)
            daily["precipitation_mm"] = daily["precipitation_mm"].round(2)
            daily_frames.append(
                daily[
                    [
                        "zone_id",
                        "zone_label",
                        "latitude",
                        "longitude",
                        "date",
                        "temperature_c",
                        "precipitation_mm",
                    ]
                ]
            )

            daily["year"] = daily["date"].dt.year.astype(int)
            daily["month"] = daily["date"].dt.month.astype(int)
            monthly = (
                daily.groupby(["year", "month"], as_index=False)
                .agg(
                    temperature_c=("temperature_c", "mean"),
                    precipitation_mm=("precipitation_mm", "sum"),
                    climate_days=("date", "size"),
                    temperature_days=("temperature_c", "count"),
                    precipitation_days=("precipitation_mm", "count"),
                )
                .copy()
            )
            monthly.loc[monthly["precipitation_days"] == 0, "precipitation_mm"] = np.nan
            monthly["days_in_month"] = monthly.apply(
                lambda row: calendar.monthrange(int(row["year"]), int(row["month"]))[1],
                axis=1,
            )
            monthly["month_name"] = monthly["month"].map(
                lambda value: calendar.month_abbr[int(value)]
            )
            monthly["month_start"] = pd.to_datetime(
                {
                    "year": monthly["year"],
                    "month": monthly["month"],
                    "day": 1,
                },
                utc=True,
            )
            monthly["year_month"] = monthly["month_start"].dt.strftime("%Y-%m")
            monthly["temperature_c"] = monthly["temperature_c"].round(2)
            monthly["precipitation_mm"] = monthly["precipitation_mm"].round(2)
            monthly["zone_id"] = zone.zone_id
            monthly["zone_label"] = zone.zone_label
            monthly["latitude"] = float(zone.latitude)
            monthly["longitude"] = float(zone.longitude)
            monthly_frames.append(
                monthly[
                    [
                        "zone_id",
                        "zone_label",
                        "latitude",
                        "longitude",
                        "year",
                        "month",
                        "month_name",
                        "year_month",
                        "temperature_c",
                        "precipitation_mm",
                        "climate_days",
                        "temperature_days",
                        "precipitation_days",
                        "days_in_month",
                    ]
                ]
            )

            if verbose:
                print(
                    f"  Open-Meteo zone {position}/{len(zones)}: "
                    f"{zone.zone_id} ({zone.dominant_country}) retrieved"
                )
        except (requests.RequestException, RuntimeError, ValueError, KeyError) as exc:
            failures.append(
                {
                    "zone_id": str(zone.zone_id),
                    "zone_label": str(zone.zone_label),
                    "latitude": float(zone.latitude),
                    "longitude": float(zone.longitude),
                    "error": str(exc),
                }
            )
            if verbose:
                print(
                    f"  Open-Meteo zone {position}/{len(zones)} failed for "
                    f"{zone.zone_id}: {exc}"
                )
        time.sleep(0.35)

    if not monthly_frames:
        raise RuntimeError("Open-Meteo failed for every core vector zone.")

    monthly_climate = (
        pd.concat(monthly_frames, ignore_index=True)
        .sort_values(["zone_id", "year", "month"])
        .reset_index(drop=True)
    )
    daily_climate = (
        pd.concat(daily_frames, ignore_index=True)
        .sort_values(["zone_id", "date"])
        .reset_index(drop=True)
    )
    climate_meta = {
        "source_name": "Open-Meteo Historical Weather API",
        "source_url": OPEN_METEO_ARCHIVE,
        "homepage": OPEN_METEO_SOURCE,
        "retrieved_at_utc": _utc_now_iso(),
        "period": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
            "complete_calendar_months": 12,
        },
        "daily_parameters": ["temperature_2m_mean", "precipitation_sum"],
        "zones_requested": int(len(zones)),
        "zones_succeeded": int(len(monthly_frames)),
        "failures": failures,
        "aggregation": (
            "Monthly temperature is the arithmetic mean of daily ERA5/ERA5-Land "
            "2 m air-temperature means; monthly precipitation is the sum of daily "
            "precipitation totals at the core-zone centroid."
        ),
    }
    return monthly_climate, daily_climate, climate_meta


def build_metadata(
    gbif_meta: dict[str, Any],
    zones: pd.DataFrame,
    climate_monthly: pd.DataFrame,
    climate_meta: dict[str, Any],
    grid_degrees: float,
) -> dict[str, Any]:
    """Assemble a provenance record for the dashboard snapshot."""
    primary_zone = zones.iloc[0].to_dict()
    zone_climate = climate_monthly[
        climate_monthly["zone_id"] == primary_zone["zone_id"]
    ]
    mean_temperature = (
        round(float(zone_climate["temperature_c"].mean()), 2)
        if not zone_climate.empty and zone_climate["temperature_c"].notna().any()
        else None
    )
    total_precipitation = (
        round(float(zone_climate["precipitation_mm"].sum()), 2)
        if not zone_climate.empty and zone_climate["precipitation_mm"].notna().any()
        else None
    )

    primary_climate = {
        "zone_id": str(primary_zone["zone_id"]),
        "zone_label": str(primary_zone["zone_label"]),
        "dominant_country": str(primary_zone["dominant_country"]),
        "latitude": round(float(primary_zone["latitude"]), 5),
        "longitude": round(float(primary_zone["longitude"]), 5),
        "sample_occurrences": int(primary_zone["sample_occurrences"]),
        "mean_temperature_c": mean_temperature,
        "total_precipitation_mm": total_precipitation,
    }

    return {
        "application": (
            "BioVector: Quantitative Biodiversity Informatics & "
            "Zoonotic Niche Modeler"
        ),
        "pipeline_version": PIPELINE_VERSION,
        "generated_at_utc": _utc_now_iso(),
        "species": gbif_meta["taxon"],
        "occurrence_dataset": {
            key: value for key, value in gbif_meta.items() if key not in {"taxon"}
        },
        "analysis": {
            "zone_count": int(len(zones)),
            "grid_degrees": float(grid_degrees),
            "primary_zone": primary_climate,
        },
        "climate_dataset": climate_meta,
        "endpoints": {
            "gbif": "https://api.gbif.org/v1/occurrence/search",
            "gbif_homepage": GBIF_SOURCE,
            "open_meteo_archive": OPEN_METEO_ARCHIVE,
            "open_meteo_homepage": OPEN_METEO_SOURCE,
        },
        "disclaimer": (
            "Occurrence data are presence-only and reflect heterogeneous sampling "
            "effort. Seasonal climate associations are descriptive and should not "
            "be interpreted as causal effect estimates."
        ),
    }


def _atomic_csv(frame: pd.DataFrame, destination: Path) -> None:
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8")
    os.replace(temporary, destination)


def _atomic_json(payload: dict[str, Any], destination: Path) -> None:
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    os.replace(temporary, destination)


def write_bundle(
    occurrences: pd.DataFrame,
    country_totals: pd.DataFrame,
    monthly_totals: pd.DataFrame,
    basis_totals: pd.DataFrame,
    zones: pd.DataFrame,
    climate_monthly: pd.DataFrame,
    climate_daily: pd.DataFrame,
    metadata: dict[str, Any],
    output_dir: Path = DATA_DIR,
) -> dict[str, Path]:
    """Write the validated, analysis-ready data bundle to disk."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    paths = {
        "occurrences": output_dir / "occurrences.csv",
        "country_totals": output_dir / "country_totals.csv",
        "monthly_totals": output_dir / "monthly_totals.csv",
        "basis_totals": output_dir / "basis_totals.csv",
        "zones": output_dir / "core_zones.csv",
        "climate_monthly": output_dir / "climate_monthly.csv",
        "climate_daily": output_dir / "climate_daily.csv",
        "metadata": output_dir / "metadata.json",
    }

    _atomic_csv(occurrences, paths["occurrences"])
    _atomic_csv(country_totals, paths["country_totals"])
    _atomic_csv(monthly_totals, paths["monthly_totals"])
    _atomic_csv(basis_totals, paths["basis_totals"])
    _atomic_csv(zones, paths["zones"])
    _atomic_csv(climate_monthly, paths["climate_monthly"])
    _atomic_csv(climate_daily, paths["climate_daily"])
    _atomic_json(metadata, paths["metadata"])
    return paths


def run_pipeline(
    max_records: int = DEFAULT_MAX_RECORDS,
    start_year: int = DEFAULT_START_YEAR,
    zone_count: int = DEFAULT_ZONE_COUNT,
    grid_degrees: float = DEFAULT_GRID_DEGREES,
    output_dir: Path = DATA_DIR,
    verbose: bool = True,
) -> dict[str, Any]:
    """Run the complete live ingestion, climate retrieval and persistence workflow."""
    end_year = date.today().year

    if verbose:
        print(f"Resolving accepted GBIF taxon for {SPECIES_NAME}...")
    gbif_result = fetch_occurrences(
        max_records=max_records,
        start_year=start_year,
        end_year=end_year,
        verbose=verbose,
    )
    occurrences = gbif_result["occurrences"]
    gbif_meta = gbif_result["metadata"]

    if verbose:
        print(
            f"Validated {len(occurrences):,} sampled occurrence records across "
            f"{gbif_meta['unique_countries']} countries."
        )

    zones = derive_core_zones(
        occurrences, zone_count=zone_count, grid_degrees=grid_degrees
    )
    if verbose:
        print(
            f"Identified {len(zones)} core vector zones from "
            f"{grid_degrees:g}° grid cells."
        )

    climate_start, climate_end = last_complete_climate_period()
    if verbose:
        print(
            "Fetching Open-Meteo climate for "
            f"{climate_start.isoformat()} to {climate_end.isoformat()}..."
        )
    climate_monthly, climate_daily, climate_meta = fetch_climate_profiles(
        zones,
        start_date=climate_start,
        end_date=climate_end,
        verbose=verbose,
    )

    metadata = build_metadata(
        gbif_meta=gbif_meta,
        zones=zones,
        climate_monthly=climate_monthly,
        climate_meta=climate_meta,
        grid_degrees=grid_degrees,
    )
    paths = write_bundle(
        occurrences=occurrences,
        country_totals=gbif_result["country_totals"],
        monthly_totals=gbif_result["monthly_totals"],
        basis_totals=gbif_result["basis_totals"],
        zones=zones,
        climate_monthly=climate_monthly,
        climate_daily=climate_daily,
        metadata=metadata,
        output_dir=output_dir,
    )

    if verbose:
        print(f"Snapshot written to {Path(output_dir).resolve()}")
        for name, path in paths.items():
            print(f"  {name}: {path.name}")

    return {
        **gbif_result,
        "zones": zones,
        "climate_monthly": climate_monthly,
        "climate_daily": climate_daily,
        "metadata": metadata,
        "paths": paths,
    }


def _bounded_integer(minimum: int, maximum: int):
    def parser(value: str) -> int:
        parsed = int(value)
        if not minimum <= parsed <= maximum:
            raise argparse.ArgumentTypeError(
                f"value must be between {minimum} and {maximum}"
            )
        return parsed

    return parser


def _bounded_float(minimum: float, maximum: float):
    def parser(value: str) -> float:
        parsed = float(value)
        if not minimum <= parsed <= maximum:
            raise argparse.ArgumentTypeError(
                f"value must be between {minimum} and {maximum}"
            )
        return parsed

    return parser


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch real Aedes aegypti occurrences from GBIF and reanalysis climate "
            "from Open-Meteo into the BioVector data directory."
        )
    )
    parser.add_argument(
        "--max-records",
        type=_bounded_integer(300, 30_000),
        default=DEFAULT_MAX_RECORDS,
        help=f"Maximum cleaned GBIF records to pull (default: {DEFAULT_MAX_RECORDS:,}).",
    )
    parser.add_argument(
        "--start-year",
        type=_bounded_integer(1900, date.today().year),
        default=DEFAULT_START_YEAR,
        help=f"First occurrence year in the GBIF query (default: {DEFAULT_START_YEAR}).",
    )
    parser.add_argument(
        "--zones",
        type=_bounded_integer(1, 12),
        default=DEFAULT_ZONE_COUNT,
        help=f"Number of core vector zones for climate retrieval (default: {DEFAULT_ZONE_COUNT}).",
    )
    parser.add_argument(
        "--grid",
        type=_bounded_float(1.0, 20.0),
        default=DEFAULT_GRID_DEGREES,
        help=f"Grid-cell size in degrees for density clustering (default: {DEFAULT_GRID_DEGREES:g}).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DATA_DIR,
        help=f"Destination directory (default: {DATA_DIR}).",
    )
    args = parser.parse_args()

    run_pipeline(
        max_records=args.max_records,
        start_year=args.start_year,
        zone_count=args.zones,
        grid_degrees=args.grid,
        output_dir=args.output_dir,
        verbose=True,
    )


if __name__ == "__main__":
    main()
