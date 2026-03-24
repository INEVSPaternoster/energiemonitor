import re
from datetime import datetime, timedelta

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

st.set_page_config(
    page_title="Energiemonitor Deutschland",
    layout="wide",
    initial_sidebar_state="collapsed",
)

HEADERS = {"User-Agent": "Mozilla/5.0 (Energiemonitor)"}

AGSI_API_KEY = st.secrets.get("AGSI_API_KEY", "")
TANKERKOENIG_API_KEY = st.secrets.get("TANKERKOENIG_API_KEY", "")

ZEIT_URL = "https://www.zeit.de/wirtschaft/energiemonitor-strompreis-gaspreis-erneuerbare-energien-ausbau"

# =========================================================
# STYLING
# =========================================================
st.markdown(
    """
    <style>
    .block-container {
        padding-top: 1.2rem;
        padding-bottom: 2rem;
        max-width: 1500px;
    }

    .app-title {
        font-size: 2rem;
        font-weight: 700;
        margin-bottom: 1.4rem;
        color: #f5f5f5;
    }

    .card-title {
        font-size: 1.35rem;
        font-weight: 700;
        margin-bottom: 0.7rem;
        line-height: 1.15;
    }

    .metric-line {
        display: flex;
        align-items: baseline;
        gap: 0.35rem;
        margin-bottom: 0.25rem;
        flex-wrap: wrap;
    }

    .metric-value {
        font-size: 2.0rem;
        font-weight: 800;
        line-height: 1;
    }

    .metric-unit {
        font-size: 1.05rem;
        font-weight: 700;
        opacity: 0.95;
    }

    .metric-text {
        font-size: 0.92rem;
        line-height: 1.3;
        margin-bottom: 0.7rem;
    }

    .small-note {
        color: #9f9f9f;
        font-size: 0.82rem;
        margin-top: 0.15rem;
        margin-bottom: 0.6rem;
        line-height: 1.35;
    }

    .empty-note {
        color: #c8c8c8;
        font-size: 0.92rem;
        margin-top: 0.15rem;
        opacity: 0.9;
    }

    .cyan { color: #5ee7f2; }
    .yellow { color: #e8d11a; }
    .green { color: #19d36b; }
    .pink { color: #ff6b8b; }
    .purple { color: #a58cff; }
    .white { color: #f5f5f5; }

    .section-gap {
        height: 10px;
    }

    .card-divider {
        border-top: 1px solid #2d2d2d;
        margin-top: 0.2rem;
        margin-bottom: 0.8rem;
    }

    .debug-box {
        margin-top: 1rem;
        padding: 1rem;
        border: 1px solid #2d2d2d;
        background: #111111;
    }

    div[data-testid="stPlotlyChart"] {
        background: transparent !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown('<div class="app-title">Energiemonitor Deutschland</div>', unsafe_allow_html=True)

# =========================================================
# HELPERS
# =========================================================
def safe_get(url, headers=None, timeout=20):
    r = requests.get(url, headers=headers or HEADERS, timeout=timeout)
    r.raise_for_status()
    return r


def safe_get_json(url, headers=None, timeout=20):
    return safe_get(url, headers=headers, timeout=timeout).json()


def as_numeric(series):
    return pd.to_numeric(series, errors="coerce")


def parse_time_any(values):
    parsed = pd.to_datetime(values, errors="coerce", utc=True)
    try:
        if hasattr(parsed, "dt"):
            parsed = parsed.dt.tz_convert("Europe/Berlin")
        else:
            parsed = parsed.tz_convert("Europe/Berlin")
    except Exception:
        pass
    return parsed


def latest_label(df, time_col="time"):
    if df is None or df.empty or time_col not in df.columns:
        return "keine Daten"
    last = pd.to_datetime(df[time_col], errors="coerce").max()
    if pd.isna(last):
        return "keine Daten"
    try:
        return last.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return str(last)


def simple_card_header(title, color_class):
    st.markdown(
        f'<div class="card-title {color_class}">{title}</div>',
        unsafe_allow_html=True,
    )


def metric_block(value, unit, text, color_class):
    st.markdown(
        f"""
        <div class="metric-line">
            <div class="metric-value {color_class}">{value}</div>
            <div class="metric-unit {color_class}">{unit}</div>
        </div>
        <div class="metric-text {color_class}">{text}</div>
        """,
        unsafe_allow_html=True,
    )


def small_note(text):
    st.markdown(f'<div class="small-note">{text}</div>', unsafe_allow_html=True)


def empty_block(text="Derzeit keine Daten verfügbar"):
    st.markdown(f'<div class="empty-note">{text}</div>', unsafe_allow_html=True)


def plot_line(df, x, y, color_hex):
    fig = px.line(df, x=x, y=y)
    fig.update_traces(line=dict(color=color_hex, width=2.5))
    fig.update_layout(
        height=140,
        margin=dict(l=6, r=6, t=6, b=6),
        paper_bgcolor="#000000",
        plot_bgcolor="#000000",
        font=dict(color="#d7d7d7"),
        xaxis=dict(
            title="",
            showgrid=False,
            zeroline=False,
            showline=False,
            tickfont=dict(size=9),
        ),
        yaxis=dict(
            title="",
            showgrid=True,
            gridcolor="#313131",
            zeroline=False,
            showline=False,
            tickfont=dict(size=9),
        ),
        showlegend=False,
    )
    return fig


def plot_area(df_long, x, y, color, color_map):
    fig = px.area(df_long, x=x, y=y, color=color, color_discrete_map=color_map)
    fig.update_layout(
        height=140,
        margin=dict(l=6, r=6, t=6, b=6),
        paper_bgcolor="#000000",
        plot_bgcolor="#000000",
        font=dict(color="#d7d7d7"),
        xaxis=dict(
            title="",
            showgrid=False,
            zeroline=False,
            showline=False,
            tickfont=dict(size=9),
        ),
        yaxis=dict(
            title="",
            showgrid=True,
            gridcolor="#313131",
            zeroline=False,
            showline=False,
            tickfont=dict(size=9),
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0,
            bgcolor="rgba(0,0,0,0)",
            font=dict(size=8),
        ),
    )
    return fig


# =========================================================
# DATA: ZEIT SNAPSHOT
# =========================================================
@st.cache_data(ttl=3600)
def load_zeit_snapshot():
    html = safe_get(ZEIT_URL, timeout=15).text
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)

    result = {
        "gas_ct_kwh": None,
        "strom_ct_kwh": None,
        "sprit_eur_l": None,
    }

    gas_match = re.search(r"Gas kostet derzeit\s+([0-9]+(?:[.,][0-9]+)?)\s*Cent je Kilowattstunde", text)
    if gas_match:
        result["gas_ct_kwh"] = float(gas_match.group(1).replace(",", "."))

    strom_match = re.search(r"Strom kostet derzeit\s+([0-9]+(?:[.,][0-9]+)?)\s*Cent je Kilowattstunde", text)
    if strom_match:
        result["strom_ct_kwh"] = float(strom_match.group(1).replace(",", "."))

    sprit_match = re.search(r"Ein Liter Benzin der Sorte Super E5 kostet\s+([0-9]+(?:[.,][0-9]+)?)\s*Euro", text)
    if sprit_match:
        result["sprit_eur_l"] = float(sprit_match.group(1).replace(",", "."))

    return result


# =========================================================
# DATA: SMARD
# =========================================================
@st.cache_data(ttl=3600)
def load_smard_strompreis():
    filter_id = 4169
    region = "DE"
    resolution = "hour"

    idx_url = f"https://www.smard.de/app/chart_data/{filter_id}/{region}/index_{resolution}.json"
    idx_data = safe_get_json(idx_url)
    timestamps = idx_data.get("timestamps", [])
    if not timestamps:
        raise RuntimeError("SMARD: keine Timestamps gefunden.")

    latest_ts = timestamps[-1]
    data_url = (
        f"https://www.smard.de/app/chart_data/{filter_id}/{region}/"
        f"{filter_id}_{region}_{resolution}_{latest_ts}.json"
    )
    data = safe_get_json(data_url)
    series = data.get("series", [])
    if not series:
        raise RuntimeError("SMARD: keine Zeitreihe gefunden.")

    df = pd.DataFrame(series, columns=["timestamp", "strompreis_eur_mwh"])
    df["time"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.tz_convert("Europe/Berlin")
    df["strompreis_eur_mwh"] = as_numeric(df["strompreis_eur_mwh"])
    return df[["time", "strompreis_eur_mwh"]].dropna().sort_values("time")


# =========================================================
# DATA: ENERGY CHARTS
# =========================================================
def parse_energy_charts_payload(data):
    if isinstance(data, dict):
        production_types = data.get("production_types")
        unix_seconds = data.get("unix_seconds")
        time_values = data.get("time")

        if isinstance(unix_seconds, list) and isinstance(production_types, list):
            df = pd.DataFrame({
                "time": pd.to_datetime(unix_seconds, unit="s", utc=True).tz_convert("Europe/Berlin")
            })
            for item in production_types:
                if not isinstance(item, dict):
                    continue
                name = item.get("name", "Unbekannt")
                values = item.get("data", [])
                if isinstance(values, list) and len(values) == len(df):
                    df[name] = pd.to_numeric(values, errors="coerce")
            if len(df.columns) > 1:
                return df.sort_values("time")

        if isinstance(time_values, list) and isinstance(production_types, list):
            df = pd.DataFrame({"time": parse_time_any(time_values)})
            for item in production_types:
                if not isinstance(item, dict):
                    continue
                name = item.get("name", "Unbekannt")
                values = item.get("data", [])
                if isinstance(values, list) and len(values) == len(df):
                    df[name] = pd.to_numeric(values, errors="coerce")
            if len(df.columns) > 1:
                return df.sort_values("time")

        if isinstance(time_values, list):
            df = pd.DataFrame({"time": parse_time_any(time_values)})
            for key, values in data.items():
                if key in ["time", "last_update", "deprecated", "production_types"]:
                    continue
                if isinstance(values, list) and len(values) == len(df):
                    df[key] = pd.to_numeric(values, errors="coerce")
            if len(df.columns) > 1:
                return df.sort_values("time")

        if isinstance(unix_seconds, list):
            df = pd.DataFrame({
                "time": pd.to_datetime(unix_seconds, unit="s", utc=True).tz_convert("Europe/Berlin")
            })
            for key, values in data.items():
                if key in ["unix_seconds", "last_update", "deprecated", "production_types"]:
                    continue
                if isinstance(values, list) and len(values) == len(df):
                    df[key] = pd.to_numeric(values, errors="coerce")
            if len(df.columns) > 1:
                return df.sort_values("time")

    if isinstance(data, list) and data:
        df = pd.DataFrame(data)
        for candidate in ["time", "date", "datetime", "timestamp", "unix_seconds"]:
            if candidate in df.columns:
                if candidate == "unix_seconds":
                    df["time"] = pd.to_datetime(df[candidate], unit="s", utc=True).dt.tz_convert("Europe/Berlin")
                else:
                    df["time"] = parse_time_any(df[candidate])
                break

        if "time" in df.columns:
            for col in df.columns:
                if col != "time":
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            return df.sort_values("time")

    raise RuntimeError(
        f"Energy-Charts-Datenformat nicht erkannt: "
        f"{list(data.keys())[:20] if isinstance(data, dict) else type(data)}"
    )


@st.cache_data(ttl=3600)
def load_energy_charts_raw(endpoint, days=7):
    end_date = datetime.utcnow().date()
    start_date = end_date - timedelta(days=days)
    url = f"https://api.energy-charts.info/{endpoint}?country=de&start={start_date.isoformat()}&end={end_date.isoformat()}"
    return safe_get_json(url)


@st.cache_data(ttl=3600)
def load_stromproduktion():
    last_error = None
    for endpoint in ["total_power", "public_power"]:
        try:
            raw = load_energy_charts_raw(endpoint, days=7)
            df = parse_energy_charts_payload(raw)
            if not df.empty and len(df.columns) > 1:
                return endpoint, raw, df
        except Exception as e:
            last_error = f"{endpoint}: {e}"
    raise RuntimeError(last_error or "Stromproduktion konnte nicht geladen werden.")


@st.cache_data(ttl=3600)
def load_installed_power():
    raw = load_energy_charts_raw("installed_power", days=365)
    df = parse_energy_charts_payload(raw)
    if df.empty or len(df.columns) <= 1:
        raise RuntimeError("Installed Power leer oder unbrauchbar.")
    return raw, df


def build_renewable_share(df_total):
    renewable_keywords = ["solar", "pv", "wind", "hydro", "water", "biomass", "renewable", "geothermal"]
    value_cols = [c for c in df_total.columns if c != "time"]
    renewable_cols = [c for c in value_cols if any(k in c.lower() for k in renewable_keywords)]

    if not renewable_cols:
        raise RuntimeError(f"Keine erneuerbaren Spalten erkannt. Vorhanden: {value_cols}")

    df = df_total.copy()
    df["gesamt"] = df[value_cols].sum(axis=1, skipna=True)
    df["erneuerbare_summe"] = df[renewable_cols].sum(axis=1, skipna=True)
    df["erneuerbaren_anteil_prozent"] = (df["erneuerbare_summe"] / df["gesamt"]) * 100
    return df, renewable_cols


def build_ausbau_series(df_installed):
    cols = [c for c in df_installed.columns if c != "time"]

    wind_cols = [c for c in cols if any(k in c.lower() for k in ["wind", "onshore", "offshore"])]
    solar_cols = [c for c in cols if any(k in c.lower() for k in ["solar", "pv", "photovoltaic"])]

    out = df_installed[["time"]].copy()
    if wind_cols:
        out["wind_gesamt_mw"] = df_installed[wind_cols].sum(axis=1, skipna=True)
    if solar_cols:
        out["solar_gesamt_mw"] = df_installed[solar_cols].sum(axis=1, skipna=True)

    return out, wind_cols, solar_cols


# =========================================================
# OPTIONAL SOURCES
# =========================================================
@st.cache_data(ttl=1800)
def load_spritpreis():
    if not TANKERKOENIG_API_KEY:
        return None

    cities = [
        ("Berlin", 52.5200, 13.4050),
        ("Hamburg", 53.5511, 9.9937),
        ("München", 48.1351, 11.5820),
        ("Köln", 50.9375, 6.9603),
        ("Frankfurt", 50.1109, 8.6821),
        ("Leipzig", 51.3397, 12.3731),
    ]

    prices = []
    for _, lat, lng in cities:
        url = (
            "https://creativecommons.tankerkoenig.de/json/list.php"
            f"?lat={lat}&lng={lng}&rad=8&sort=dist&type=all&apikey={TANKERKOENIG_API_KEY}"
        )
        data = safe_get_json(url)
        if not data.get("ok", False):
            continue
        for station in data.get("stations", []):
            price = station.get("e5")
            if isinstance(price, (int, float)) and price > 0:
                prices.append(price)

    if not prices:
        return None

    return pd.DataFrame({
        "time": [pd.Timestamp.now(tz="Europe/Berlin")],
        "spritpreis_eur_l": [sum(prices) / len(prices)]
    })


@st.cache_data(ttl=3600)
def load_gasspeicher():
    if not AGSI_API_KEY:
        return None

    headers = {"x-key": AGSI_API_KEY, "User-Agent": "Mozilla/5.0"}
    data = safe_get_json("https://agsi.gie.eu/api?country=DE", headers=headers)
    rows = data.get("data", [])
    if not rows:
        return None

    df = pd.DataFrame(rows)
    if "gasDayStart" in df.columns:
        df["time"] = pd.to_datetime(df["gasDayStart"])
    elif "date" in df.columns:
        df["time"] = pd.to_datetime(df["date"])
    else:
        return None

    if "full" in df.columns:
        df["full"] = as_numeric(df["full"])

    return df.sort_values("time")


# =========================================================
# LOAD DATA
# =========================================================
errors = {}

try:
    df_strompreis = load_smard_strompreis()
except Exception as e:
    df_strompreis = None
    errors["strompreis"] = str(e)

try:
    zeit_snapshot = load_zeit_snapshot()
except Exception as e:
    zeit_snapshot = None
    errors["zeit_snapshot"] = str(e)

try:
    strom_endpoint, strom_raw, df_total = load_stromproduktion()
except Exception as e:
    strom_endpoint, strom_raw, df_total = None, None, None
    errors["stromproduktion"] = str(e)

try:
    raw_installed, df_installed = load_installed_power()
except Exception as e:
    raw_installed, df_installed = None, None
    errors["installed_power"] = str(e)

try:
    df_ren, ren_cols = build_renewable_share(df_total) if df_total is not None else (None, [])
except Exception as e:
    df_ren, ren_cols = None, []
    errors["erneuerbare"] = str(e)

try:
    df_ausbau, wind_cols, solar_cols = build_ausbau_series(df_installed) if df_installed is not None else (None, [], [])
except Exception as e:
    df_ausbau, wind_cols, solar_cols = None, [], []
    errors["ausbau"] = str(e)

try:
    df_sprit = load_spritpreis()
except Exception as e:
    df_sprit = None
    errors["spritpreis"] = str(e)

try:
    df_storage = load_gasspeicher()
except Exception as e:
    df_storage = None
    errors["gasspeicher"] = str(e)

# =========================================================
# PREP DISPLAY DATA
# =========================================================
strommix_color_map = {
    "Coal": "#c49a86",
    "Lignite": "#b38b78",
    "Solar": "#e8d11a",
    "Gas": "#8a76e8",
    "Biomass": "#97b30f",
    "Wind": "#27c6d9",
    "Hydro": "#4ab6ff",
}

mix_keep = []
if df_total is not None:
    preferred = ["Coal", "Lignite", "Solar", "Gas", "Biomass", "Wind", "Hydro", "Offshore", "Onshore"]
    cols = [c for c in df_total.columns if c != "time"]
    for p in preferred:
        for c in cols:
            if p.lower() in c.lower() and c not in mix_keep:
                mix_keep.append(c)
    mix_keep = mix_keep[:6]

# =========================================================
# ROW 1
# =========================================================
row1 = st.columns(4, gap="small")

with row1[0]:
    simple_card_header("Windkraftausbau", "cyan")

    if df_ausbau is not None and "wind_gesamt_mw" in df_ausbau.columns and not df_ausbau["wind_gesamt_mw"].dropna().empty:
        latest = df_ausbau["wind_gesamt_mw"].dropna().iloc[-1]
        first = df_ausbau["wind_gesamt_mw"].dropna().iloc[0]
        delta = latest - first

        metric_block(f"{delta/1000:.1f}", "GW", "wurden seit Jahresbeginn erreicht", "cyan")
        small_note(f"Installierte Gesamtleistung: {latest/1000:.1f} GW<br>Stand: {latest_label(df_ausbau)}")
        fig = plot_line(df_ausbau.tail(60), "time", "wind_gesamt_mw", "#5ee7f2")
        st.plotly_chart(fig, use_container_width=True)
    else:
        empty_block()

with row1[1]:
    simple_card_header("Solarausbau", "yellow")

    if df_ausbau is not None and "solar_gesamt_mw" in df_ausbau.columns and not df_ausbau["solar_gesamt_mw"].dropna().empty:
        latest = df_ausbau["solar_gesamt_mw"].dropna().iloc[-1]
        first = df_ausbau["solar_gesamt_mw"].dropna().iloc[0]
        delta = latest - first

        metric_block(f"{delta/1000:.1f}", "GW", "wurden seit Jahresbeginn installiert", "yellow")
        small_note(f"Installierte Gesamtleistung: {latest/1000:.1f} GW<br>Stand: {latest_label(df_ausbau)}")
        fig = plot_line(df_ausbau.tail(60), "time", "solar_gesamt_mw", "#e8d11a")
        st.plotly_chart(fig, use_container_width=True)
    else:
        empty_block()

with row1[2]:
    simple_card_header("Erneuerbare", "green")

    if df_ren is not None and not df_ren.empty and not df_ren["erneuerbaren_anteil_prozent"].dropna().empty:
        latest = df_ren["erneuerbaren_anteil_prozent"].dropna().iloc[-1]
        avg30 = df_ren["erneuerbaren_anteil_prozent"].tail(24 * 30).mean()

        metric_block(f"{latest:.0f}", "%", "des Stroms waren zuletzt erneuerbar", "green")
        small_note(f"30-Tage-Durchschnitt: {avg30:.0f} %<br>Stand: {latest_label(df_ren)}")
        fig = plot_line(df_ren.tail(24 * 30), "time", "erneuerbaren_anteil_prozent", "#19d36b")
        st.plotly_chart(fig, use_container_width=True)
    else:
        empty_block()

with row1[3]:
    simple_card_header("Stromproduktion", "white")

    if df_total is not None and mix_keep:
        latest_mix = df_total.iloc[-1]
        total_now = latest_mix[mix_keep].sum(skipna=True)

        metric_block(f"{total_now/1000:.1f}", "GW", "Strom wurden zuletzt in Deutschland erzeugt", "white")

        mix_long = df_total[["time"] + mix_keep].tail(24 * 3).melt(
            id_vars="time",
            var_name="Serie",
            value_name="Wert"
        )
        fig = plot_area(mix_long, "time", "Wert", "Serie", strommix_color_map)
        st.plotly_chart(fig, use_container_width=True)

        latest_shares = latest_mix[mix_keep] / latest_mix[mix_keep].sum(skipna=True) * 100
        latest_shares = latest_shares.sort_values(ascending=False)
        share_lines = [f"{idx}: {val:.0f} %" for idx, val in latest_shares.head(6).items()]
        small_note(f"Anteil der Energieträger<br>{'<br>'.join(share_lines)}")
    else:
        empty_block()

st.markdown('<div class="section-gap"></div>', unsafe_allow_html=True)

# =========================================================
# ROW 2
# =========================================================
row2 = st.columns(4, gap="small")

with row2[0]:
    simple_card_header("Strompreis", "cyan")

    if df_strompreis is not None and not df_strompreis.empty and not df_strompreis["strompreis_eur_mwh"].dropna().empty:
        latest = df_strompreis["strompreis_eur_mwh"].dropna().iloc[-1]
        latest_ct = latest / 10.0

        metric_block(f"{latest_ct:.1f}", "Cent", "kostete eine kWh Strom am Großhandelsmarkt zuletzt", "cyan")
        small_note(f"Stündliche Werte<br>Stand: {latest_label(df_strompreis)}")
        fig = plot_line(df_strompreis.tail(24 * 7), "time", "strompreis_eur_mwh", "#5ee7f2")
        st.plotly_chart(fig, use_container_width=True)
    elif zeit_snapshot and zeit_snapshot.get("strom_ct_kwh") is not None:
        metric_block(
            f"{zeit_snapshot['strom_ct_kwh']:.1f}",
            "Cent",
            "kostete eine kWh Strom für Neukunden zuletzt",
            "cyan",
        )
        small_note("Quelle: ZEIT-Snapshot")
    else:
        empty_block()

with row2[1]:
    simple_card_header("Spritpreis", "pink")

    if df_sprit is not None and not df_sprit.empty:
        latest = df_sprit["spritpreis_eur_l"].iloc[-1]
        metric_block(f"{latest:.2f}", "€", "kostete ein Liter Super E5 zuletzt im Mittel", "pink")
        small_note(f"Mittel aus mehreren Stadtabfragen<br>Stand: {latest_label(df_sprit)}")
        fig = plot_line(df_sprit, "time", "spritpreis_eur_l", "#ff6b8b")
        st.plotly_chart(fig, use_container_width=True)
    elif zeit_snapshot and zeit_snapshot.get("sprit_eur_l") is not None:
        latest = zeit_snapshot["sprit_eur_l"]
        metric_block(f"{latest:.2f}", "€", "kostete ein Liter Super E5 zuletzt im Mittel", "pink")
        small_note("Fallback: ZEIT-Snapshot")
    else:
        empty_block()

with row2[2]:
    simple_card_header("Gaspreis", "purple")

    if zeit_snapshot and zeit_snapshot.get("gas_ct_kwh") is not None:
        latest = zeit_snapshot["gas_ct_kwh"]
        metric_block(f"{latest:.1f}", "Cent", "kostete eine kWh Gas für Neukunden zuletzt", "purple")
        small_note("Aktueller Snapshot")
    else:
        empty_block()

with row2[3]:
    simple_card_header("Füllstand", "purple")

    if df_storage is not None and "full" in df_storage.columns and not df_storage["full"].dropna().empty:
        latest = df_storage["full"].dropna().iloc[-1]
        metric_block(f"{latest:.1f}", "%", "der Gasspeicher waren zuletzt gefüllt", "purple")
        small_note(f"Tageswerte<br>Stand: {latest_label(df_storage)}")
        fig = plot_line(df_storage.tail(120), "time", "full", "#a58cff")
        st.plotly_chart(fig, use_container_width=True)
    else:
        empty_block("Kein Gasspeicher verfügbar oder API-Key fehlt")

# =========================================================
# DEBUG
# =========================================================
with st.expander("Debug / Fehlermeldungen"):
    st.markdown('<div class="debug-box">', unsafe_allow_html=True)
    if errors:
        st.json(errors)
    else:
        st.write("Keine Fehler erkannt.")
    st.markdown("</div>", unsafe_allow_html=True)
