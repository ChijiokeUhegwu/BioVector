"""BioVector: a quantitative biodiversity and One Health dashboard.

The application reads a locally cached, provenance-rich snapshot produced by
``ingest.py``. It can also rebuild that snapshot directly from GBIF and
Open-Meteo, with Streamlit caching protecting both public APIs.
"""

from __future__ import annotations

import calendar
import html
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import ingest


# Page configuration and visual system
st.set_page_config(
    page_title="BioVector | Biodiversity & Zoonotic Risk Tracker",
    layout="wide",
    initial_sidebar_state="expanded",
)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SNAPSHOT_FILES = (
    "occurrences.csv",
    "country_totals.csv",
    "monthly_totals.csv",
    "basis_totals.csv",
    "core_zones.csv",
    "climate_monthly.csv",
    "climate_daily.csv",
    "metadata.json",
)
SAMPLE_SIZE = ingest.DEFAULT_MAX_RECORDS
ZONE_COUNT = ingest.DEFAULT_ZONE_COUNT
GRID_DEGREES = ingest.DEFAULT_GRID_DEGREES
START_YEAR = ingest.DEFAULT_START_YEAR

COLORS = {
    "ink": "#173A34",
    "muted": "#5B6E67",
    "teal": "#1F7367",
    "teal_light": "#8CC4B4",
    "amber": "#E49B45",
    "coral": "#D96B4F",
    "sky": "#7FB7C9",
    "cream": "#F6F3EA",
    "white": "#FFFFFF",
    "line": "rgba(23, 58, 52, 0.12)",
}
BASIS_COLORS = {
    "Human Observation": "#1F7367",
    "Material Citation": "#7C6FA6",
    "Preserved Specimen": "#D9A441",
    "Material Sample": "#D96B4F",
}
FALLBACK_COLORS = ["#5C8D89", "#A38BB5", "#D5A85A", "#C77C61"]

st.markdown(
    """
    <style>
    :root {
        --ink: #173A34;
        --muted: #5B6E67;
        --teal: #1F7367;
        --teal-soft: #8CC4B4;
        --amber: #E49B45;
        --cream: #F6F3EA;
        --paper: rgba(255, 255, 255, 0.88);
        --line: rgba(23, 58, 52, 0.12);
    }
    html, body, [class*="css"] {
        font-family: "Segoe UI", "Inter", system-ui, -apple-system, sans-serif;
    }
    .stApp {
        background:
            radial-gradient(circle at 85% 4%, rgba(140, 196, 180, 0.20), transparent 27rem),
            radial-gradient(circle at 7% 30%, rgba(228, 155, 69, 0.10), transparent 25rem),
            #F7F5EF;
    }
    header[data-testid="stHeader"] {
        background: transparent;
    }
    .block-container {
        max-width: 1450px;
        padding-top: 1.2rem;
        padding-bottom: 2.5rem;
    }
    [data-testid="stSidebar"] {
        background: #EEF1E9;
        border-right: 1px solid var(--line);
    }
    [data-testid="stSidebar"] .block-container {
        padding-top: 1.5rem;
    }
    .hero {
        display: grid;
        grid-template-columns: minmax(0, 1.75fr) minmax(250px, 0.65fr);
        gap: 2.2rem;
        position: relative;
        overflow: hidden;
        margin-bottom: 14px;
        padding: 2.35rem 2.5rem;
        border: 1px solid rgba(255, 255, 255, 0.14);
        border-radius: 26px;
        background:
            radial-gradient(circle at 88% 10%, rgba(140, 196, 180, 0.22), transparent 28rem),
            radial-gradient(circle at 0% 100%, rgba(228, 155, 69, 0.15), transparent 24rem),
            linear-gradient(132deg, #102F2E 0%, #173F3B 55%, #0D2B2B 100%);
        box-shadow: 0 22px 55px rgba(23, 58, 52, 0.20);
        color: #F7F5EF;
    }
    .hero:after {
        content: "";
        position: absolute;
        width: 20rem;
        height: 20rem;
        right: -8rem;
        bottom: -10rem;
        border: 1px solid rgba(255, 255, 255, 0.12);
        border-radius: 50%;
        box-shadow:
            0 0 0 3rem rgba(255, 255, 255, 0.025),
            0 0 0 6rem rgba(255, 255, 255, 0.018);
        pointer-events: none;
    }
    .hero-copy { position: relative; z-index: 1; }
    .eyebrow {
        margin-bottom: 0.7rem;
        color: #A9D5C7;
        font-size: 0.71rem;
        font-weight: 800;
        letter-spacing: 0.17em;
        text-transform: uppercase;
    }
    .hero h1 {
        margin: 0;
        color: #FAF8F1;
        font-family: "Iowan Old Style", "Palatino Linotype", Georgia, serif;
        font-size: clamp(2.7rem, 4.8vw, 4.5rem);
        font-weight: 600;
        letter-spacing: -0.045em;
        line-height: 0.96;
    }
    .hero-subtitle {
        max-width: 850px;
        margin: 0.7rem 0 0;
        color: #C8E1D8;
        font-size: clamp(1rem, 1.5vw, 1.23rem);
        font-weight: 500;
        line-height: 1.45;
    }
    .hero-overview {
        max-width: 860px;
        margin: 1.15rem 0 0;
        color: rgba(247, 245, 239, 0.82);
        font-size: 0.95rem;
        line-height: 1.72;
    }
    .hero-badges {
        display: flex;
        flex-wrap: wrap;
        gap: 0.48rem;
        margin-top: 1.45rem;
    }
    .hero-badge {
        padding: 0.38rem 0.68rem;
        border: 1px solid rgba(255, 255, 255, 0.17);
        border-radius: 999px;
        background: rgba(255, 255, 255, 0.065);
        color: #E7F3EE;
        font-size: 0.72rem;
        letter-spacing: 0.025em;
    }
    .hero-panel {
        position: relative;
        z-index: 1;
        align-self: center;
        padding: 1.25rem 1.3rem;
        border: 1px solid rgba(255, 255, 255, 0.16);
        border-radius: 18px;
        background: rgba(255, 255, 255, 0.075);
        backdrop-filter: blur(10px);
    }
    .hero-panel-label {
        margin-bottom: 0.9rem;
        color: #A9D5C7;
        font-size: 0.68rem;
        font-weight: 800;
        letter-spacing: 0.14em;
        text-transform: uppercase;
    }
    .hero-kv {
        display: flex;
        justify-content: space-between;
        gap: 1rem;
        padding: 0.52rem 0;
        border-top: 1px solid rgba(255, 255, 255, 0.10);
        font-size: 0.79rem;
    }
    .hero-kv:first-of-type { border-top: 0; }
    .hero-kv span { color: rgba(247, 245, 239, 0.60); }
    .hero-kv strong { color: #F8F5EC; font-weight: 650; text-align: right; }
    .section-head {
        margin: 2.6rem 0 1rem;
        padding-bottom: 0.7rem;
        border-bottom: 1px solid var(--line);
    }
    .section-kicker {
        margin-bottom: 0.32rem;
        color: #9A6A30;
        font-size: 0.67rem;
        font-weight: 800;
        letter-spacing: 0.16em;
        text-transform: uppercase;
    }
    .section-head h2 {
        margin: 0;
        color: var(--ink);
        font-family: "Iowan Old Style", "Palatino Linotype", Georgia, serif;
        font-size: clamp(1.55rem, 2.4vw, 2.2rem);
        font-weight: 600;
        letter-spacing: -0.025em;
        line-height: 1.15;
    }
    .section-head p {
        max-width: 970px;
        margin: 0.5rem 0 0;
        color: var(--muted);
        font-size: 0.91rem;
        line-height: 1.62;
    }
    [data-testid="stMetric"] {
        min-height: 123px;
        padding: 1rem 1.08rem;
        border: 1px solid var(--line);
        border-radius: 17px;
        background: var(--paper);
        box-shadow: 0 9px 24px rgba(23, 58, 52, 0.045);
    }
    [data-testid="stMetricLabel"] p {
        color: #5D706A;
        font-size: 0.71rem !important;
        font-weight: 750 !important;
        letter-spacing: 0.075em;
        text-transform: uppercase;
    }
    [data-testid="stMetricValue"] {
        color: var(--ink);
        font-family: "Iowan Old Style", "Palatino Linotype", Georgia, serif;
        font-size: 1.82rem;
        font-weight: 600;
        letter-spacing: -0.035em;
    }
    .metric-caption {
        margin-top: -0.25rem;
        margin-bottom: 0.25rem;
        color: #687A74;
        font-size: 0.73rem;
        line-height: 1.4;
    }
    [data-testid="stPlotlyChart"] {
        overflow: hidden;
        border: 1px solid var(--line);
        border-radius: 18px;
        background: rgba(255, 255, 255, 0.82);
        box-shadow: 0 9px 28px rgba(23, 58, 52, 0.04);
    }
    .chart-title {
        margin: 0.2rem 0 0.08rem;
        color: var(--ink);
        font-size: 1.02rem;
        font-weight: 760;
        letter-spacing: -0.015em;
    }
    .chart-subtitle {
        margin: 0 0 0.35rem;
        color: var(--muted);
        font-size: 0.78rem;
        line-height: 1.45;
    }
    .signal {
        margin-top: 0.8rem;
        padding: 1.2rem 1.35rem;
        border: 1px solid rgba(31, 115, 103, 0.18);
        border-left: 4px solid var(--teal);
        border-radius: 14px;
        background: linear-gradient(100deg, rgba(140, 196, 180, 0.17), rgba(255, 255, 255, 0.54));
    }
    .signal-label {
        margin-bottom: 0.35rem;
        color: var(--teal);
        font-size: 0.68rem;
        font-weight: 800;
        letter-spacing: 0.14em;
        text-transform: uppercase;
    }
    .signal p {
        margin: 0;
        color: #294A43;
        font-size: 0.93rem;
        line-height: 1.65;
    }
    .side-brand {
        margin-bottom: 0.1rem;
        color: var(--ink);
        font-family: "Iowan Old Style", "Palatino Linotype", Georgia, serif;
        font-size: 1.75rem;
        font-weight: 600;
        letter-spacing: -0.04em;
    }
    .side-tag {
        margin-bottom: 1.1rem;
        color: #97682D;
        font-size: 0.63rem;
        font-weight: 800;
        letter-spacing: 0.13em;
        text-transform: uppercase;
    }
    .side-section {
        margin: 1.25rem 0 0.45rem;
        color: #53665F;
        font-size: 0.66rem;
        font-weight: 800;
        letter-spacing: 0.13em;
        text-transform: uppercase;
    }
    .side-note {
        color: #687A74;
        font-size: 0.72rem;
        line-height: 1.5;
    }
    .method-note {
        padding: 0.8rem 1rem;
        border-radius: 12px;
        background: rgba(228, 155, 69, 0.10);
        color: #5B4B35;
        font-size: 0.82rem;
        line-height: 1.55;
    }
    .footer {
        margin-top: 2.2rem;
        padding-top: 1.1rem;
        border-top: 1px solid var(--line);
        color: #77847F;
        font-size: 0.72rem;
        line-height: 1.6;
    }
    .stButton > button, .stDownloadButton > button {
        border-radius: 10px;
        font-weight: 650;
    }
    .stButton > button[kind="primary"] {
        border-color: var(--teal);
        background: var(--teal);
        color: #FFFFFF;
    }
    .stButton > button[kind="primary"]:hover {
        border-color: #185C53;
        background: #185C53;
    }
    [data-testid="stExpander"] {
        border: 1px solid var(--line);
        border-radius: 14px;
        background: rgba(255, 255, 255, 0.62);
    }
    div[data-baseweb="select"] > div {
        border-radius: 10px;
        border-color: var(--line);
        background: rgba(255, 255, 255, 0.78);
    }
    @media (max-width: 920px) {
        .hero {
            grid-template-columns: 1fr;
            padding: 1.7rem 1.45rem;
        }
        .hero-panel { display: none; }
        .block-container { padding-top: 0.7rem; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# Data loading, provenance and API caching
def snapshot_signature() -> str | None:
    """Return a cache key derived from the current data files."""
    parts = []
    for name in SNAPSHOT_FILES:
        path = DATA_DIR / name
        if not path.exists():
            return None
        stat = path.stat()
        parts.append(f"{name}:{stat.st_size}:{stat.st_mtime_ns}")
    return "|".join(parts)


@st.cache_data(show_spinner=False)
def load_snapshot(data_dir: str, signature: str) -> dict[str, Any]:
    """Load the persisted analysis snapshot without repeating network calls."""
    base = Path(data_dir)
    occurrences = pd.read_csv(
        base / "occurrences.csv",
        dtype={
            "gbifID": "string",
            "scientificName": "string",
            "countryCode": "string",
            "country": "string",
            "datePrecision": "string",
            "basisOfRecord": "string",
        },
    )
    occurrences["eventDate"] = pd.to_datetime(
        occurrences["eventDate"], errors="coerce", utc=True
    )
    occurrences["decimalLatitude"] = pd.to_numeric(
        occurrences["decimalLatitude"], errors="coerce"
    )
    occurrences["decimalLongitude"] = pd.to_numeric(
        occurrences["decimalLongitude"], errors="coerce"
    )
    occurrences = occurrences.dropna(
        subset=["eventDate", "decimalLatitude", "decimalLongitude"]
    ).copy()

    country_totals = pd.read_csv(
        base / "country_totals.csv", dtype={"countryCode": "string", "country": "string"}
    )
    monthly_totals = pd.read_csv(base / "monthly_totals.csv")
    basis_totals = pd.read_csv(base / "basis_totals.csv")
    zones = pd.read_csv(base / "core_zones.csv")
    climate_monthly = pd.read_csv(base / "climate_monthly.csv")
    metadata = json.loads((base / "metadata.json").read_text(encoding="utf-8"))

    for column in ("year", "month"):
        occurrences[column] = pd.to_numeric(occurrences[column], errors="coerce")

    country_totals["gbif_records"] = pd.to_numeric(
        country_totals["gbif_records"], errors="coerce"
    ).fillna(0)
    country_totals["sample_records"] = pd.to_numeric(
        country_totals["sample_records"], errors="coerce"
    ).fillna(0)
    country_totals["sample_fraction_pct"] = pd.to_numeric(
        country_totals["sample_fraction_pct"], errors="coerce"
    ).fillna(0)
    monthly_totals["sample_records"] = pd.to_numeric(
        monthly_totals["sample_records"], errors="coerce"
    ).fillna(0)
    monthly_totals["month"] = pd.to_numeric(
        monthly_totals["month"], errors="coerce"
    ).astype("Int64")
    basis_totals["gbif_records"] = pd.to_numeric(
        basis_totals["gbif_records"], errors="coerce"
    ).fillna(0)

    for column in ("latitude", "longitude", "sample_occurrences", "share_pct"):
        zones[column] = pd.to_numeric(zones[column], errors="coerce")
    for column in (
        "temperature_c",
        "precipitation_mm",
        "climate_days",
        "temperature_days",
        "precipitation_days",
        "days_in_month",
    ):
        if column in climate_monthly:
            climate_monthly[column] = pd.to_numeric(
                climate_monthly[column], errors="coerce"
            )
    climate_monthly["year"] = pd.to_numeric(
        climate_monthly["year"], errors="coerce"
    ).astype("Int64")
    climate_monthly["month"] = pd.to_numeric(
        climate_monthly["month"], errors="coerce"
    ).astype("Int64")

    required_occurrence_columns = {
        "decimalLatitude",
        "decimalLongitude",
        "country",
        "eventDate",
        "datePrecision",
        "basisOfRecord",
        "month",
    }
    if not required_occurrence_columns.issubset(occurrences.columns):
        raise ValueError("The occurrence snapshot is missing required columns.")
    if occurrences.empty:
        raise ValueError("The occurrence snapshot contains no valid records.")
    if country_totals.empty or monthly_totals.empty or zones.empty:
        raise ValueError("The aggregate analysis tables are incomplete.")

    return {
        "occurrences": occurrences,
        "country_totals": country_totals,
        "monthly_totals": monthly_totals,
        "basis_totals": basis_totals,
        "zones": zones,
        "climate_monthly": climate_monthly,
        "metadata": metadata,
    }


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def fetch_gbif_live(
    max_records: int,
    start_year: int,
    end_year: int,
) -> dict[str, Any]:
    """Cached wrapper around the live GBIF fetch for the refresh workflow."""
    return ingest.fetch_occurrences(
        max_records=max_records,
        start_year=start_year,
        end_year=end_year,
        verbose=False,
    )


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def fetch_climate_live(
    zone_rows: tuple[tuple[Any, ...], ...],
    start_date: str,
    end_date: str,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Cached wrapper around the live Open-Meteo fetch for the refresh workflow."""
    columns = [
        "zone_id",
        "zone_label",
        "latitude",
        "longitude",
        "sample_occurrences",
        "share_pct",
        "dominant_country",
        "grid_cell",
    ]
    zones = pd.DataFrame(list(zone_rows), columns=columns)
    return ingest.fetch_climate_profiles(
        zones,
        start_date=date.fromisoformat(start_date),
        end_date=date.fromisoformat(end_date),
        verbose=False,
    )


def build_live_snapshot() -> dict[str, Any]:
    """Rebuild all datasets from the two live APIs and persist a new snapshot."""
    gbif_data = fetch_gbif_live(
        max_records=SAMPLE_SIZE,
        start_year=START_YEAR,
        end_year=date.today().year,
    )
    occurrences = gbif_data["occurrences"]
    zones = ingest.derive_core_zones(
        occurrences,
        zone_count=ZONE_COUNT,
        grid_degrees=GRID_DEGREES,
    )
    zone_rows = tuple(
        zones[
            [
                "zone_id",
                "zone_label",
                "latitude",
                "longitude",
                "sample_occurrences",
                "share_pct",
                "dominant_country",
                "grid_cell",
            ]
        ].itertuples(index=False, name=None)
    )
    climate_start, climate_end = ingest.last_complete_climate_period()
    climate_monthly, climate_daily, climate_meta = fetch_climate_live(
        zone_rows,
        climate_start.isoformat(),
        climate_end.isoformat(),
    )
    metadata = ingest.build_metadata(
        gbif_meta=gbif_data["metadata"],
        zones=zones,
        climate_monthly=climate_monthly,
        climate_meta=climate_meta,
        grid_degrees=GRID_DEGREES,
    )
    ingest.write_bundle(
        occurrences=occurrences,
        country_totals=gbif_data["country_totals"],
        monthly_totals=gbif_data["monthly_totals"],
        basis_totals=gbif_data["basis_totals"],
        zones=zones,
        climate_monthly=climate_monthly,
        climate_daily=climate_daily,
        metadata=metadata,
        output_dir=DATA_DIR,
    )
    return {
        "occurrences": occurrences,
        "country_totals": gbif_data["country_totals"],
        "monthly_totals": gbif_data["monthly_totals"],
        "basis_totals": gbif_data["basis_totals"],
        "zones": zones,
        "climate_monthly": climate_monthly,
        "metadata": metadata,
    }


def initialize_bundle() -> dict[str, Any]:
    """Load the local snapshot, or build the first snapshot from live APIs."""
    signature = snapshot_signature()
    bundle: dict[str, Any] | None = None
    load_error: str | None = None

    if signature:
        try:
            bundle = load_snapshot(str(DATA_DIR), signature)
        except Exception as exc:
            load_error = str(exc)

    if load_error:
        st.warning(
            "The local snapshot could not be read and will be rebuilt from the "
            f"live APIs. Details: {load_error}"
        )

    if bundle is None:
        placeholder = st.empty()
        placeholder.info(
            "No complete local snapshot was found. Building the first live "
            "BioVector snapshot from GBIF and Open-Meteo. This can take a few minutes."
        )
        try:
            with st.spinner("Fetching, validating and modelling live biodiversity data..."):
                bundle = build_live_snapshot()
            placeholder.empty()
        except Exception as exc:
            placeholder.empty()
            st.error(f"BioVector could not initialize from live APIs: {exc}")
            st.stop()

    return bundle


# Formatting and analytical helpers
def get_nested(mapping: dict[str, Any], *keys: str, default: Any = None) -> Any:
    current: Any = mapping
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def format_integer(value: Any) -> str:
    try:
        if value is None or pd.isna(value):
            return "n/a"
        return f"{int(round(float(value))):,}"
    except (TypeError, ValueError):
        return "n/a"


def format_decimal(value: Any, digits: int = 1, suffix: str = "") -> str:
    try:
        if value is None or pd.isna(value):
            return "n/a"
        return f"{float(value):,.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return "n/a"


def compact_number(value: Any) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if numeric >= 1_000_000:
        return f"{numeric / 1_000_000:.1f}M"
    if numeric >= 1_000:
        return f"{numeric / 1_000:.1f}k"
    return f"{numeric:.0f}"


def format_timestamp(value: Any) -> str:
    if not value:
        return "n/a"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc).strftime("%d %b %Y · %H:%M UTC")
    except (TypeError, ValueError):
        return str(value)


def style_figure(
    figure: go.Figure,
    height: int,
    margin: dict[str, int] | None = None,
) -> go.Figure:
    figure.update_layout(
        height=height,
        margin=margin or {"l": 24, "r": 24, "t": 24, "b": 24},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(
            family='"Segoe UI", "Inter", system-ui, sans-serif',
            color=COLORS["ink"],
            size=12,
        ),
        hoverlabel=dict(
            bgcolor="#173A34",
            bordercolor="#173A34",
            font=dict(color="#FFFFFF", size=12),
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0,
            bgcolor="rgba(0,0,0,0)",
            font=dict(size=10),
        ),
    )
    figure.update_xaxes(
        showgrid=False,
        zeroline=False,
        linecolor="rgba(23,58,52,0.12)",
        tickfont=dict(color=COLORS["muted"], size=11),
        title_font=dict(color=COLORS["muted"], size=11),
    )
    figure.update_yaxes(
        showgrid=True,
        gridcolor="rgba(23,58,52,0.07)",
        zeroline=False,
        linecolor="rgba(23,58,52,0.12)",
        tickfont=dict(color=COLORS["muted"], size=11),
        title_font=dict(color=COLORS["muted"], size=11),
    )
    return figure


def section_heading(kicker: str, title: str, description: str) -> None:
    st.markdown(
        f"""
        <div class="section-head">
            <div class="section-kicker">{html.escape(kicker)}</div>
            <h2>{html.escape(title)}</h2>
            <p>{html.escape(description)}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def spearman_correlation(left: pd.Series, right: pd.Series) -> float | None:
    aligned = pd.concat([left, right], axis=1).dropna()
    if len(aligned) < 3 or aligned.iloc[:, 0].nunique() < 2 or aligned.iloc[:, 1].nunique() < 2:
        return None
    # Spearman rank correlation is the Pearson correlation of the fractional ranks.
    # Computing it via rank().corr(method='pearson') avoids requiring an external scipy installation.
    coefficient = aligned.iloc[:, 0].rank().corr(aligned.iloc[:, 1].rank(), method="pearson")
    return None if pd.isna(coefficient) else float(coefficient)


def circular_lag(reference: int, target: int) -> int:
    return ((reference - target + 6) % 12) - 6


# Bundle initialization and shared derived values
bundle = initialize_bundle()
occurrences = bundle["occurrences"]
country_totals = bundle["country_totals"].sort_values(
    "gbif_records", ascending=False
).reset_index(drop=True)
monthly_totals = bundle["monthly_totals"].sort_values("month").reset_index(drop=True)
basis_totals = bundle["basis_totals"].sort_values(
    "gbif_records", ascending=False
).reset_index(drop=True)
zones = bundle["zones"].sort_values("zone_id").reset_index(drop=True)
climate_monthly = bundle["climate_monthly"].copy()
metadata = bundle["metadata"]

taxon = metadata.get("species", {})
occurrence_meta = metadata.get("occurrence_dataset", {})
climate_meta = metadata.get("climate_dataset", {})
analysis_meta = metadata.get("analysis", {})
primary_zone_meta = analysis_meta.get("primary_zone", {})

query_total = int(
    occurrence_meta.get("query_total")
    or pd.to_numeric(country_totals["gbif_records"], errors="coerce").sum()
)
sampled_records = int(len(occurrences))
countries_available = int(len(country_totals))
sampled_countries = int(
    occurrence_meta.get("unique_countries")
    or occurrences["countryCode"].nunique(dropna=True)
)
top_country = country_totals.iloc[0]
top_country_share = (
    float(top_country["gbif_records"]) / query_total * 100.0 if query_total else 0.0
)
sample_fraction = sampled_records / query_total * 100.0 if query_total else 0.0
primary_temp = primary_zone_meta.get("mean_temperature_c")
query_years = get_nested(occurrence_meta, "query_parameters", "year", default="2000–present")
climate_start = get_nested(climate_meta, "period", "start", default="")
climate_end = get_nested(climate_meta, "period", "end", default="")
snapshot_time = format_timestamp(metadata.get("generated_at_utc"))
species_name = taxon.get("scientific_name", "Aedes aegypti (Linnaeus, 1762)")
taxon_key = taxon.get("taxon_key", "1651891")

climate_zone_ids = set(climate_monthly["zone_id"].dropna().astype(str))
zone_options = zones[
    zones["zone_id"].astype(str).isin(climate_zone_ids)
].reset_index(drop=True)
if zone_options.empty:
    zone_options = zones.reset_index(drop=True)


# Sidebar controls and data lineage
with st.sidebar:
    st.markdown(
        '<div class="side-brand">BioVector</div>'
        '<div class="side-tag">Biodiversity · Climate · One Health</div>',
        unsafe_allow_html=True,
    )

    st.markdown('<div class="side-section">Study system</div>', unsafe_allow_html=True)
    st.markdown(f"***{species_name}***")
    st.caption(
        "A globally invasive container-breeding mosquito and a principal vector "
        "of dengue, Zika, chikungunya and yellow fever viruses."
    )
    st.markdown(
        f"<div class='side-note'>GBIF backbone taxon key "
        f"<strong>{html.escape(str(taxon_key))}</strong><br>"
        f"Query window {html.escape(str(query_years))}</div>",
        unsafe_allow_html=True,
    )

    st.markdown('<div class="side-section">Snapshot</div>', unsafe_allow_html=True)
    st.markdown(
        f"<div class='side-note'>{html.escape(snapshot_time)}<br>"
        f"{format_integer(sampled_records)} sampled records from "
        f"{format_integer(sampled_countries)} countries<br>"
        f"{format_integer(query_total)} records in the full query across "
        f"{format_integer(countries_available)} countries<br>"
        f"{format_integer(len(zones))} core vector zones</div>",
        unsafe_allow_html=True,
    )

    st.markdown('<div class="side-section">Data operations</div>', unsafe_allow_html=True)
    refresh_clicked = st.button(
        "Rebuild from live APIs",
        type="primary",
        width="stretch",
        help=(
            "Fetch current GBIF occurrence facets and a country-stratified "
            "coordinate sample, then refresh Open-Meteo climate for the core zones."
        ),
    )
    if refresh_clicked:
        st.cache_data.clear()
        refresh_placeholder = st.empty()
        refresh_placeholder.info("Rebuilding the live snapshot. Please keep this tab open.")
        try:
            with st.spinner("Fetching GBIF records and Open-Meteo climate..."):
                build_live_snapshot()
        except Exception as exc:
            refresh_placeholder.empty()
            st.error(f"Live refresh failed: {exc}")
        else:
            refresh_placeholder.success("Snapshot rebuilt from the live APIs.")
            st.rerun()

    st.caption(
        "Public API calls are cached for six hours during a session. "
        f"The current spatial model uses a {format_decimal(sample_fraction, 1, '%')} "
        "country-stratified sample of the full GBIF query."
    )

    st.markdown(
        """#### Taxon Audit
BioVector targets Aedes aegypti using the accepted GBIF Taxonomic Key 1651891. This ensures precise taxonomic filtering and guards against species misidentification, preventing data from being mixed with sibling vector taxa (such as Aedes albopictus)."""
    )

    st.markdown('<div class="side-section">Source endpoints</div>', unsafe_allow_html=True)
    st.markdown(
        "[GBIF Occurrence API](https://api.gbif.org/v1/occurrence/search)  \n"
        "[Open-Meteo Historical Weather](https://archive-api.open-meteo.com/v1/archive)"
    )

    with st.expander("Download sampled occurrences"):
        st.download_button(
            "Download CSV",
            occurrences.to_csv(index=False).encode("utf-8"),
            file_name="biovector_occurrence_sample.csv",
            mime="text/csv",
            width="stretch",
        )


# App header and headline indicators
st.markdown(
    """### BioVector: Biodiversity & Zoonotic Risk Tracker
This dashboard connects live data from the Global Biodiversity Information Facility (GBIF) with real-world weather records. The goal is to map exactly where the Aedes aegypti mosquito is found in relation to local temperature and rainfall, to see how environmental changes help this vector thrive. 
*Note: This tracker shows where the mosquito has been spotted and recorded by researchers; it is a map of documented surveillance, not a total count of every mosquito in existence.*"""
)

st.markdown(
    """#### Taxon Audit
BioVector targets Aedes aegypti using the accepted GBIF Taxonomic Key 1651891. This ensures precise taxonomic filtering and guards against species misidentification, preventing data from being mixed with sibling vector taxa (such as Aedes albopictus)."""
)

kpi_columns = st.columns(4, gap="medium")
with kpi_columns[0]:
    st.metric(
        "GBIF records pulled",
        format_integer(sampled_records),
        help="Validated occurrence records with coordinates and dates used for mapping.",
    )
    st.markdown(
        f'<div class="metric-caption">{format_decimal(sample_fraction, 1, "%")} of the full query</div>',
        unsafe_allow_html=True,
    )
with kpi_columns[1]:
    st.metric(
        "Query universe",
        format_integer(query_total),
        help="All GBIF records matching the georeferenced, present, 2000–present query.",
    )
    st.markdown(
        f'<div class="metric-caption">{format_integer(countries_available)} countries in the source facets</div>',
        unsafe_allow_html=True,
    )
with kpi_columns[2]:
    st.metric(
        "Leading country",
        str(top_country["country"]),
        help="Country with the most records in the complete GBIF query facet.",
    )
    st.markdown(
        f'<div class="metric-caption">{format_integer(top_country["gbif_records"])} records · '
        f'{format_decimal(top_country_share, 1, "%")} of query</div>',
        unsafe_allow_html=True,
    )
with kpi_columns[3]:
    st.metric(
        "Hotspot mean air temp",
        format_decimal(primary_temp, 1, " °C"),
        help="12-month average of daily mean 2 m air temperature at the densest sampled grid cell.",
    )
    st.markdown(
        f'<div class="metric-caption">{html.escape(str(primary_zone_meta.get("zone_label", "Primary zone")))}</div>',
        unsafe_allow_html=True,
    )


# GIS map of real occurrence coordinates
section_heading(
    "01 / Spatial evidence",
    "The recorded geography of a global vector",
    "Every point is a georeferenced GBIF occurrence from the validated coordinate sample. "
    f"Core vector zones are the densest {GRID_DEGREES:g}° geographic cells and anchor the climate analysis.",
)

map_trace_class = getattr(go, "Scattermapbox", None) or go.Scattermap
map_layout_key = "mapbox" if hasattr(go, "Scattermapbox") else "map"

observed_date = occurrences["eventDate"].dt.strftime("%d %b %Y")
month_precision_date = occurrences["eventDate"].dt.strftime("%b %Y")
year_precision_date = occurrences["year"].astype("Int64").astype("string")
display_date = np.select(
    [
        occurrences["datePrecision"].eq("year").fillna(False),
        occurrences["datePrecision"].eq("month").fillna(False),
    ],
    [year_precision_date.fillna("Unknown date"), month_precision_date.fillna("Unknown date")],
    default=observed_date.fillna("Unknown date"),
)
occurrences = occurrences.assign(displayDate=display_date)

map_figure = go.Figure()
basis_series = occurrences["basisOfRecord"].fillna("Unknown").value_counts()
for index, (basis, _) in enumerate(basis_series.items()):
    subset = occurrences[occurrences["basisOfRecord"].fillna("Unknown") == basis]
    color = BASIS_COLORS.get(basis, FALLBACK_COLORS[index % len(FALLBACK_COLORS)])
    map_figure.add_trace(
        map_trace_class(
            lat=subset["decimalLatitude"],
            lon=subset["decimalLongitude"],
            mode="markers",
            name=basis,
            marker=dict(size=4.6, color=color, opacity=0.62),
            customdata=np.column_stack(
                [
                    subset["country"].fillna("Unassigned"),
                    subset["displayDate"],
                ]
            ),
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "%{customdata[1]}<br>"
                f"{html.escape(str(basis))}<extra></extra>"
            ),
        )
    )

zone_customdata = np.column_stack(
    [
        zones["zone_label"].astype(str),
        zones["sample_occurrences"],
        zones["share_pct"],
    ]
)
map_figure.add_trace(
    map_trace_class(
        lat=zones["latitude"],
        lon=zones["longitude"],
        mode="markers+text",
        name="Core vector zones",
        text=zones["zone_id"],
        textposition="top center",
        textfont=dict(color=COLORS["ink"], size=10),
        marker=dict(
            size=14,
            color=COLORS["amber"],
            opacity=0.95,
            allowoverlap=True,
        ),
        customdata=zone_customdata,
        hovertemplate=(
            "<b>%{customdata[0]}</b><br>"
            "Sampled records: %{customdata[1]:,}<br>"
            "Share of sample: %{customdata[2]:.1f}%<extra></extra>"
        ),
    )
)

map_figure.update_layout(
    **{
        map_layout_key: dict(
            style="open-street-map",
            center=dict(lat=18, lon=8),
            zoom=1.05,
        )
    },
    height=575,
    margin=dict(l=0, r=0, t=0, b=0),
    paper_bgcolor="rgba(0,0,0,0)",
    legend=dict(
        orientation="h",
        yanchor="bottom",
        y=1.01,
        xanchor="left",
        x=0,
        bgcolor="rgba(255,255,255,0.78)",
        bordercolor="rgba(23,58,52,0.12)",
        borderwidth=1,
        font=dict(size=10, color=COLORS["ink"]),
    ),
    hoverlabel=dict(
        bgcolor="#173A34",
        bordercolor="#173A34",
        font=dict(color="#FFFFFF", size=12),
    ),
)
st.plotly_chart(
    map_figure,
    width="stretch",
    config={"displayModeBar": False, "scrollZoom": False},
)
st.caption(
    "Amber markers identify core vector zones. The coordinate sample is country-stratified; "
    "year-only records remain visible here but are not assigned to a month. Country totals "
    "below use complete GBIF query facets."
)
basis_summary = " · ".join(
    f"{row.basisOfRecord} {format_decimal(row.share_pct, 1, '%')}"
    for row in basis_totals.itertuples(index=False)
)
st.caption(f"Complete-query basis-of-record composition: {basis_summary}.")


# Country density and seasonal observation profile
month_dated_total = int(monthly_totals["sample_records"].sum())
section_heading(
    "02 / Density & seasonality",
    "Where observation effort concentrates",
    "Country totals use the complete GBIF query. The seasonal profile uses sampled records with "
    "month-level event dates, because year-only source records cannot be assigned to a month without "
    "fabricating precision. Reporting intensity is shaped by surveillance capacity and is not true abundance.",
)

density_col, season_col = st.columns([1.08, 1], gap="large")

with density_col:
    st.markdown(
        '<div class="chart-title">Occurrence density by country</div>'
        '<div class="chart-subtitle">Top 10 countries by complete-query GBIF record counts</div>',
        unsafe_allow_html=True,
    )
    top_countries = (
        country_totals.head(10).sort_values("gbif_records", ascending=True).copy()
    )
    top_countries["share_pct"] = (
        top_countries["gbif_records"] / query_total * 100.0 if query_total else 0.0
    )
    density_colors = [
        COLORS["amber"] if index == len(top_countries) - 1 else COLORS["teal"]
        for index in range(len(top_countries))
    ]
    density_figure = go.Figure(
        go.Bar(
            x=top_countries["gbif_records"],
            y=top_countries["country"],
            orientation="h",
            marker=dict(color=density_colors, line=dict(width=0)),
            text=[compact_number(value) for value in top_countries["gbif_records"]],
            textposition="outside",
            textfont=dict(color=COLORS["muted"], size=10),
            customdata=np.column_stack(
                [
                    top_countries["countryCode"],
                    top_countries["share_pct"],
                    top_countries["sample_records"],
                ]
            ),
            hovertemplate=(
                "<b>%{y}</b> · %{customdata[0]}<br>"
                "GBIF records: %{x:,}<br>"
                "Share of query: %{customdata[1]:.1f}%<br>"
                "Sampled records: %{customdata[2]:,}<extra></extra>"
            ),
        )
    )
    density_figure.update_layout(
        showlegend=False,
        bargap=0.29,
        xaxis_title="GBIF occurrence records",
    )
    density_figure.update_xaxes(
        range=[0, float(top_countries["gbif_records"].max()) * 1.17]
    )
    st.plotly_chart(
        style_figure(density_figure, height=388, margin=dict(l=9, r=30, t=16, b=24)),
        width="stretch",
        config={"displayModeBar": False},
    )
    st.caption(
        f"{str(top_country['country'])} alone contributes "
        f"{format_decimal(top_country_share, 1, '%')} of all records in the query."
    )

with season_col:
    st.markdown(
        '<div class="chart-title">Seasonal observation profile</div>'
        f'<div class="chart-subtitle">Month-dated sample · January–December · n = {format_integer(month_dated_total)}</div>',
        unsafe_allow_html=True,
    )
    month_lookup = monthly_totals.set_index("month")
    peak_month = int(month_lookup["sample_records"].idxmax())
    peak_month_count = int(month_lookup.loc[peak_month, "sample_records"])
    peak_month_share = (
        peak_month_count / month_dated_total * 100.0 if month_dated_total else 0.0
    )
    bar_colors = [
        COLORS["amber"] if int(month) == peak_month else COLORS["teal_light"]
        for month in monthly_totals["month"]
    ]
    seasonal_figure = go.Figure(
        go.Bar(
            x=monthly_totals["month"],
            y=monthly_totals["sample_records"],
            marker=dict(color=bar_colors, line=dict(width=0)),
            text=[compact_number(value) for value in monthly_totals["sample_records"]],
            textposition="outside",
            textfont=dict(color=COLORS["muted"], size=9),
            customdata=np.column_stack(
                [monthly_totals["month_name"], monthly_totals["share_pct"]]
            ),
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "Month-dated sample: %{y:,}<br>"
                "Share of month-dated sample: %{customdata[1]:.1f}%<extra></extra>"
            ),
        )
    )
    seasonal_figure.update_layout(
        showlegend=False,
        bargap=0.27,
        xaxis=dict(
            tickmode="array",
            tickvals=monthly_totals["month"],
            ticktext=[
                calendar.month_abbr[int(month)] for month in monthly_totals["month"]
            ],
        ),
        yaxis_title="Month-dated records",
    )
    seasonal_figure.update_yaxes(
        range=[0, float(monthly_totals["sample_records"].max()) * 1.16]
    )
    st.plotly_chart(
        style_figure(seasonal_figure, height=388, margin=dict(l=16, r=16, t=16, b=24)),
        width="stretch",
        config={"displayModeBar": False},
    )
    st.caption(
        f"Peak reporting month: {calendar.month_name[peak_month]} · "
        f"{format_integer(peak_month_count)} month-dated records · "
        f"{format_decimal(peak_month_share, 1, '%')} of the month-dated sample. "
        f"{format_integer(month_dated_total)} of {format_integer(sampled_records)} "
        "sampled records carry month-level dates."
    )


# Climate profiles for core vector zones
section_heading(
    "03 / Climate niche",
    "Environmental context at the vector's core zones",
    "Open-Meteo reanalysis supplies daily temperature and precipitation at each core-zone centroid. "
    "The chart shows the selected zone's recent climate rhythm; the niche envelope compares all modelled zones.",
)

zone_labels = zone_options["zone_label"].astype(str).tolist()
primary_zone_id = primary_zone_meta.get("zone_id")
default_index = 0
if primary_zone_id in set(zone_options["zone_id"].astype(str)):
    default_index = zone_labels.index(
        zone_options.loc[
            zone_options["zone_id"].astype(str) == str(primary_zone_id), "zone_label"
        ].iloc[0]
    )

selected_zone_label = st.selectbox(
    "Core vector zone",
    options=zone_labels,
    index=default_index,
    help="Select a high-density grid cell for the climate and phenology views.",
)
selected_zone_row = zone_options[
    zone_options["zone_label"].astype(str) == selected_zone_label
].iloc[0]
selected_zone_id = str(selected_zone_row["zone_id"])
selected_zone_climate = climate_monthly[
    climate_monthly["zone_id"].astype(str) == selected_zone_id
].copy()

annual_temp = (
    float(selected_zone_climate["temperature_c"].mean())
    if selected_zone_climate["temperature_c"].notna().any()
    else None
)
annual_precip = (
    float(selected_zone_climate["precipitation_mm"].sum())
    if selected_zone_climate["precipitation_mm"].notna().any()
    else None
)
annual_sample = int(selected_zone_row["sample_occurrences"])
zone_country = str(selected_zone_row["dominant_country"])

st.caption(
    f"{selected_zone_label} · {zone_country} · "
    f"{format_integer(annual_sample)} sampled records · "
    f"{format_decimal(annual_temp, 1, ' °C')} mean air temperature · "
    f"{format_decimal(annual_precip, 0, ' mm')} annual precipitation"
)

climate_col, envelope_col = st.columns([1.1, 0.9], gap="large")

with climate_col:
    st.markdown(
        '<div class="chart-title">Monthly climate at the selected zone</div>'
        f'<div class="chart-subtitle">{html.escape(str(climate_start))} to {html.escape(str(climate_end))}</div>',
        unsafe_allow_html=True,
    )
    if selected_zone_climate.empty:
        st.info("No Open-Meteo monthly profile is available for this zone.")
    else:
        selected_zone_climate = selected_zone_climate.sort_values(
            ["year", "month"]
        ).reset_index(drop=True)
        period_labels = [
            f"{calendar.month_abbr[int(month)]} {int(year)}"
            for year, month in zip(
                selected_zone_climate["year"].fillna(0),
                selected_zone_climate["month"].fillna(0),
            )
        ]
        climate_figure = make_subplots(specs=[[{"secondary_y": True}]])
        climate_figure.add_trace(
            go.Bar(
                x=period_labels,
                y=selected_zone_climate["precipitation_mm"],
                name="Precipitation",
                marker=dict(color="rgba(127, 183, 201, 0.68)", line=dict(width=0)),
                hovertemplate="%{x}<br>Precipitation: %{y:.1f} mm<extra></extra>",
            ),
            secondary_y=False,
        )
        climate_figure.add_trace(
            go.Scatter(
                x=period_labels,
                y=selected_zone_climate["temperature_c"],
                name="Mean air temperature",
                mode="lines+markers",
                line=dict(color=COLORS["coral"], width=2.6),
                marker=dict(size=7, color=COLORS["coral"], line=dict(width=1.5, color="#FFFFFF")),
                hovertemplate="%{x}<br>Mean temperature: %{y:.1f} °C<extra></extra>",
            ),
            secondary_y=True,
        )
        climate_figure.update_layout(hovermode="x unified", bargap=0.26)
        climate_figure.update_yaxes(
            title_text="Precipitation (mm)", secondary_y=False, rangemode="tozero"
        )
        climate_figure.update_yaxes(
            title_text="Mean air temperature (°C)", secondary_y=True, rangemode="tozero"
        )
        st.plotly_chart(
            style_figure(climate_figure, height=412, margin=dict(l=20, r=16, t=26, b=38)),
            width="stretch",
            config={"displayModeBar": False},
        )

with envelope_col:
    st.markdown(
        '<div class="chart-title">Core-zone climate envelope</div>'
        '<div class="chart-subtitle">Bubble area is proportional to the zone sample; axes show 12-month climate</div>',
        unsafe_allow_html=True,
    )
    if climate_monthly.empty:
        st.info("No core-zone climate profiles are available.")
    else:
        envelope = (
            climate_monthly.groupby(["zone_id", "zone_label"], as_index=False)
            .agg(
                mean_temperature_c=("temperature_c", "mean"),
                total_precipitation_mm=("precipitation_mm", "sum"),
            )
            .dropna(subset=["mean_temperature_c", "total_precipitation_mm"])
        )
        envelope = envelope.merge(
            zones[
                [
                    "zone_id",
                    "dominant_country",
                    "sample_occurrences",
                    "share_pct",
                ]
            ],
            on="zone_id",
            how="left",
        )
        if envelope.empty:
            st.info("No complete climate summaries are available for the core zones.")
        else:
            max_sample = max(float(envelope["sample_occurrences"].max()), 1.0)
            marker_sizes = 13.0 + 24.0 * np.sqrt(
                envelope["sample_occurrences"].fillna(0.0) / max_sample
            )
            marker_colors = [
                COLORS["amber"] if str(zone_id) == selected_zone_id else COLORS["teal"]
                for zone_id in envelope["zone_id"]
            ]
            envelope_figure = go.Figure(
                go.Scatter(
                    x=envelope["total_precipitation_mm"],
                    y=envelope["mean_temperature_c"],
                    mode="markers+text",
                    text=envelope["zone_id"].astype(str),
                    textposition="middle center",
                    textfont=dict(color="#FFFFFF", size=9),
                    marker=dict(
                        size=marker_sizes,
                        color=marker_colors,
                        opacity=0.9,
                        line=dict(width=2, color="#FFFFFF"),
                    ),
                    customdata=np.column_stack(
                        [
                            envelope["zone_label"].astype(str),
                            envelope["dominant_country"].fillna("Unassigned"),
                            envelope["sample_occurrences"].fillna(0),
                            envelope["share_pct"].fillna(0),
                        ]
                    ),
                    hovertemplate=(
                        "<b>%{customdata[0]}</b><br>"
                        "Country: %{customdata[1]}<br>"
                        "Annual rainfall: %{x:,.0f} mm<br>"
                        "Mean temperature: %{y:.1f} °C<br>"
                        "Sampled records: %{customdata[2]:,}<br>"
                        "Share of sample: %{customdata[3]:.1f}%<extra></extra>"
                    ),
                )
            )
            envelope_figure.update_layout(showlegend=False)
            envelope_figure.update_xaxes(title_text="Annual precipitation (mm)")
            envelope_figure.update_yaxes(title_text="Mean air temperature (°C)")
            st.plotly_chart(
                style_figure(
                    envelope_figure,
                    height=412,
                    margin=dict(l=20, r=16, t=26, b=38),
                ),
                width="stretch",
                config={"displayModeBar": False},
            )
            st.caption(
                "Selected zone is highlighted in amber. Climate values are reanalysis "
                "estimates at each cluster centroid."
            )


# Seasonal coupling between records and climate
section_heading(
    "04 / Phenology–climate coupling",
    "Does reporting seasonality align with hotspot climate?",
    "Spearman rank correlations compare the global month-dated sampled reporting profile with "
    "the selected hotspot's most recent 12 complete climate months. Because record precision and "
    "coverage vary by country — and the dominant Ghana dataset is year-only — this is a cross-scale "
    "surveillance association, not a local phenology or causal effect estimate.",
)

if selected_zone_climate.empty:
    st.info("Climate alignment cannot be calculated without a climate profile for this zone.")
else:
    observation_by_month = (
        monthly_totals.set_index("month")["sample_records"]
        .reindex(range(1, 13), fill_value=0)
        .astype(float)
    )
    climate_by_month = (
        selected_zone_climate.groupby("month", as_index=False)
        .agg(
            temperature_c=("temperature_c", "mean"),
            precipitation_mm=("precipitation_mm", "mean"),
        )
        .set_index("month")
        .reindex(range(1, 13))
    )
    rainfall_series = climate_by_month["precipitation_mm"]
    temperature_series = climate_by_month["temperature_c"]
    rho_rain = spearman_correlation(observation_by_month, rainfall_series)
    rho_temp = spearman_correlation(observation_by_month, temperature_series)

    peak_observation = int(observation_by_month.idxmax())
    peak_rainfall = (
        int(rainfall_series.idxmax()) if rainfall_series.notna().any() else None
    )
    peak_temperature = (
        int(temperature_series.idxmax())
        if temperature_series.notna().any()
        else None
    )
    observation_share = (
        observation_by_month.loc[peak_observation] / observation_by_month.sum() * 100.0
        if observation_by_month.sum()
        else 0.0
    )

    association_columns = st.columns(4, gap="medium")
    with association_columns[0]:
        st.metric(
            "Global peak observation",
            calendar.month_name[peak_observation],
            help="Calendar month with the largest number of month-dated sampled records across all countries.",
        )
        st.markdown(
            f'<div class="metric-caption">{format_decimal(observation_share, 1, "%")} of month-dated sample</div>',
            unsafe_allow_html=True,
        )
    with association_columns[1]:
        rain_value = (
            float(rainfall_series.loc[peak_rainfall])
            if peak_rainfall is not None
            else None
        )
        st.metric(
            "Peak local rainfall",
            calendar.month_name[peak_rainfall] if peak_rainfall else "n/a",
            help="Calendar month with the highest mean monthly rainfall in the climate window.",
        )
        st.markdown(
            f'<div class="metric-caption">{format_decimal(rain_value, 0, " mm")} monthly total</div>',
            unsafe_allow_html=True,
        )
    with association_columns[2]:
        temp_value = (
            float(temperature_series.loc[peak_temperature])
            if peak_temperature is not None
            else None
        )
        st.metric(
            "Peak local temperature",
            calendar.month_name[peak_temperature] if peak_temperature else "n/a",
            help="Calendar month with the highest mean air temperature in the climate window.",
        )
        st.markdown(
            f'<div class="metric-caption">{format_decimal(temp_value, 1, " °C")} monthly mean</div>',
            unsafe_allow_html=True,
        )
    with association_columns[3]:
        st.metric(
            "Rainfall rank correlation",
            f"{rho_rain:+.2f}" if rho_rain is not None else "n/a",
            help="Spearman correlation between monthly observation counts and monthly rainfall; n = 12 calendar months.",
        )
        st.markdown(
            f'<div class="metric-caption">ρ = {rho_rain:+.2f} · n = 12 months</div>'
            if rho_rain is not None
            else '<div class="metric-caption">Insufficient variation to estimate</div>',
            unsafe_allow_html=True,
        )

    if peak_rainfall is not None:
        lag = circular_lag(peak_observation, peak_rainfall)
        if lag == 0:
            timing_sentence = (
                f"Peak occurrence reporting coincides with the wettest month "
                f"({calendar.month_name[peak_rainfall]})."
            )
        elif lag > 0:
            timing_sentence = (
                f"Peak occurrence reporting follows the wettest month "
                f"({calendar.month_name[peak_rainfall]}) by {lag} month{'s' if lag != 1 else ''}."
            )
        else:
            timing_sentence = (
                f"Peak occurrence reporting precedes the wettest month "
                f"({calendar.month_name[peak_rainfall]}) by {abs(lag)} month{'s' if abs(lag) != 1 else ''}."
            )
    else:
        timing_sentence = "A rainfall peak could not be identified for this zone."

    rho_temp_text = f"{rho_temp:+.2f}" if rho_temp is not None else "n/a"
    if rho_rain is not None:
        correlation_clause = (
            f"local rainfall seasonality has a Spearman rank correlation of {rho_rain:+.2f} "
            f"and temperature seasonality {rho_temp_text} with the global monthly reporting profile."
        )
    else:
        correlation_clause = (
            f"temperature seasonality is {rho_temp_text} with the global monthly reporting "
            "profile; a rainfall correlation could not be estimated for this zone."
        )
    signal_text = (
        f"Across the global month-dated sample, {calendar.month_name[peak_observation]} carries "
        f"the largest reporting signal ({format_decimal(observation_share, 1, '%')} of records). "
        f"{timing_sentence} At {selected_zone_label}, {correlation_clause} The mismatch between "
        f"observation effort, vector ecology and climate seasonality is itself a One Health "
        f"surveillance signal."
    )
    st.markdown(
        f'<div class="signal"><div class="signal-label">Interpretation · one health signal</div>'
        f"<p>{html.escape(signal_text)}</p></div>",
        unsafe_allow_html=True,
    )
    st.caption(
        "The observation profile aggregates global month-dated records across all query years; "
        "climate uses the latest 12 complete months at the selected zone. The result measures "
        "cross-scale seasonal alignment, not local mosquito abundance. Rank correlations are "
        "descriptive and reported without causal interpretation."
    )


# Methods, limitations and reproducible data access
section_heading(
    "05 / Methods & provenance",
    "How the evidence was assembled",
    "The workflow is reproducible from two public sources and preserves the distinction between "
    "source-side aggregate facets, the country-stratified coordinate sample and month-dated occurrence records.",
)

with st.expander("Methods, data lineage and limitations", expanded=False):
    methods_left, methods_middle, methods_right = st.columns(3, gap="large")

    with methods_left:
        st.markdown("**Biodiversity data · GBIF**")
        st.markdown(
            f"GBIF occurrence search for the accepted *Aedes aegypti* backbone key "
            f"`{html.escape(str(taxon_key))}`. Query filters: coordinates present, "
            f"no geospatial issue, present occurrence status, years "
            f"{html.escape(str(query_years))}. "
            f"The source reports **{format_integer(query_total)}** matching records."
        )
        st.markdown(
            "Country and basis-of-record totals are complete GBIF facets. The "
            f"coordinate sample contains {format_integer(sampled_records)} records from "
            f"{format_integer(sampled_countries)} countries, allocated by a country-stratified "
            "quota. Each raw record is cleaned for valid coordinates and a parseable "
            "event date. Date precision is retained: year-only records stay on the "
            "map but are excluded from monthly analyses."
        )
        st.markdown(
            f"[GBIF occurrence API](https://api.gbif.org/v1/occurrence/search) · "
            f"[GBIF homepage](https://www.gbif.org)"
        )

    with methods_middle:
        st.markdown("**Climate data · Open-Meteo**")
        st.markdown(
            f"Daily mean 2 m air temperature and precipitation were requested for "
            f"**{format_integer(get_nested(climate_meta, 'zones_succeeded', default=0))}** "
            f"of **{format_integer(get_nested(climate_meta, 'zones_requested', default=0))}** "
            f"core zones from {html.escape(str(climate_start))} to "
            f"{html.escape(str(climate_end))}."
        )
        st.markdown(
            "Monthly temperature is the mean of daily means; monthly precipitation "
            "is the sum of daily totals. The selected seasonality window contains "
            "12 complete calendar months. No climate gaps are simulated or interpolated."
        )
        st.markdown(
            "[Open-Meteo archive API](https://archive-api.open-meteo.com/v1/archive) · "
            "[Open-Meteo homepage](https://open-meteo.com)"
        )

    with methods_right:
        st.markdown("**Analytics and interpretation**")
        st.markdown(
            f"Core zones are the densest **{GRID_DEGREES:g}°** grid cells in the "
            "coordinate sample. Country density uses complete-query facets. Seasonal "
            "alignment uses Spearman rank correlations between the global month-dated "
            "sample profile and hotspot rainfall/temperature; it is deliberately labeled "
            "cross-scale rather than local phenology."
        )
        st.markdown(
            "Presence-only records are shaped by sampling effort, taxonomic coverage "
            "and accessibility. They cannot be read as abundance. The correlation "
            "metrics summarize seasonal alignment and do not identify causal drivers."
        )
        st.markdown(
            f"Pipeline version `{html.escape(str(metadata.get('pipeline_version', 'n/a')))}` · "
            f"snapshot `{html.escape(snapshot_time)}`."
        )

    st.markdown("---")
    st.markdown(
        """#### Taxon Audit
BioVector targets Aedes aegypti using the accepted GBIF Taxonomic Key 1651891. This ensures precise taxonomic filtering and guards against species misidentification, preventing data from being mixed with sibling vector taxa (such as Aedes albopictus)."""
    )
    st.caption(
        "The dashboard uses observed GBIF records and reanalysis climate only. "
        "No occurrence coordinates, temperatures or precipitation values are synthesized "
        "or interpolated from unrelated records. Year-only event dates are explicitly "
        "flagged and excluded from every month-specific analysis."
    )

    download_columns = st.columns(3, gap="medium")
    with download_columns[0]:
        st.download_button(
            "Occurrence sample · CSV",
            occurrences.to_csv(index=False).encode("utf-8"),
            file_name="biovector_occurrences.csv",
            mime="text/csv",
            width="stretch",
        )
    with download_columns[1]:
        st.download_button(
            "Country totals · CSV",
            country_totals.to_csv(index=False).encode("utf-8"),
            file_name="biovector_country_totals.csv",
            mime="text/csv",
            width="stretch",
        )
    with download_columns[2]:
        st.download_button(
            "Metadata · JSON",
            json.dumps(metadata, indent=2, ensure_ascii=False, default=str).encode("utf-8"),
            file_name="biovector_metadata.json",
            mime="application/json",
            width="stretch",
        )


st.markdown(
    f"""
    <div class="footer">
        BioVector · Quantitative Biodiversity Informatics &amp; Zoonotic Niche Modeler.
        Built by Chijioke C. Uhegwu from real GBIF occurrence records and Open-Meteo historical climate.
        Snapshot generated {html.escape(snapshot_time)}. Designed for One Health,
        macroecology and quantitative biodiversity research workflows.
    </div>
    """,
    unsafe_allow_html=True,
)
