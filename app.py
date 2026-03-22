import streamlit as st
import pandas as pd
import requests
import plotly.express as px
from datetime import datetime, timedelta

st.set_page_config(layout="wide")
st.title("Energiemonitor Deutschland")

# =========================================================
# Secrets / API Keys
# =========================================================
AGSI_API_KEY = st.secrets.get("AGSI_API_KEY", "")
TANKERKOENIG_API_KEY = st.secrets.get("TANKERKOENIG_API_KEY", "")

# =========================================================
# Hilfsfunktionen
# =========================================================
def safe_get_json(url, headers=None, timeout=30):
    r = requests.get(url, headers=headers or {"User-Agent": "Mozilla/5.0"}, timeout=timeout)
    r.raise_for_status()
    return r.json()

def parse_smard_series(series, value_name):
    df = pd.DataFrame(series, columns=["timestamp", value_name])
    df["time"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.tz_convert("Europe/Berlin")
    return df.drop(columns=["timestamp"]).sort_values("time")

def extract_energy_charts_df(data):
    """
    Robuster Parser für unterschiedliche Energy-Charts-Formate.
    Unterstützt:
    - dict mit unix_seconds + production_types[{name,data}]
    - dict mit unix_seconds + mehreren Listen
    - list[dict]
    """
    # Fall A: production_types + unix_seconds
    if isinstance(data, dict):
        production = data.get("production_types", [])
        unix_seconds = data.get("unix_seconds", [])

        if production and unix_seconds:
            df = pd.DataFrame({
                "time": pd.to_datetime(unix_seconds, unit="s", utc=True).tz_convert("Europe/Berlin")
            })
            for item in production:
                name = item.get("name", "Unbekannt")
                values = item.get("data", [])
                if len(values) == len(df):
                    df[name] = pd.to_numeric(values, errors="coerce")
            if len(df.columns) > 1:
                return df.sort_values("time")

        # Fall B: unix_seconds + flache Listen
        if "unix_seconds" in data:
            df = pd.DataFrame({
                "time": pd.to_datetime(data["unix_seconds"], unit="s", utc=True).tz_convert("Europe/Berlin")
            })
            for key, values in data.items():
                if key == "unix_seconds":
                    continue
                if isinstance(values, list) and len(values) == len(df):
                    df[key] = pd.to_numeric(values, errors="coerce")
            if len(df.columns) > 1:
                return df.sort_values("time")

    # Fall C: Liste von Datensätzen
    if isinstance(data, list) and len(data) > 0:
        df = pd.DataFrame(data)

        for candidate in ["time", "date", "datetime", "timestamp", "unix_seconds"]:
            if candidate in df.columns:
                if candidate == "unix_seconds":
                    df["time"] = pd.to_datetime(df[candidate], unit="s", utc=True).dt.tz_convert("Europe/Berlin")
                else:
                    df["time"] = pd.to_datetime(df[candidate], utc=True, errors="coerce")
                    try:
                        df["time"] = df["time"].dt.tz_convert("Europe/Berlin")
                    except Exception:
                        pass
                break

        if "time" in df.columns:
            for col in df.columns:
                if col != "time":
                    df[col] = pd.to_numeric(df[col], errors="ignore")
            return df.sort_values("time")

    raise RuntimeError(
        f"Energy-Charts-Datenformat nicht erkannt: "
        f"{list(data.keys())[:20] if isinstance(data, dict) else type(data)}"
    )

# =========================================================
# 1) SMARD – Strompreis Day-Ahead
# =========================================================
@st.cache_data(ttl=3600)
def get_smard_day_ahead_price():
    filter_id = 4169
    region = "DE"
    resolution = "hour"

    idx_url = f"https://www.smard.de/app/chart_data/{filter_id}/{region}/index_{resolution}.json"
    idx = safe_get_json(idx_url)
    timestamps = idx.get("timestamps", [])
    if not timestamps:
        raise RuntimeError("Keine SMARD-Timestamps für Strompreis gefunden.")

    latest_ts = timestamps[-1]
    data_url = (
        f"https://www.smard.de/app/chart_data/{filter_id}/{region}/"
        f"{filter_id}_{region}_{resolution}_{latest_ts}.json"
    )
    data = safe_get_json(data_url)
    series = data.get("series", [])
    if not series:
        raise RuntimeError("Keine SMARD-Daten für Strompreis gefunden.")

    return parse_smard_series(series, "strompreis_eur_mwh")

# =========================================================
# 2) Energy-Charts – Stromproduktion / Strommix
# =========================================================
@st.cache_data(ttl=3600)
def get_energy_charts_total_power(days=7):
    end_date = datetime.utcnow().date()
    start_date = end_date - timedelta(days=days)

    url = (
        "https://api.energy-charts.info/total_power"
        f"?country=de&start={start_date.isoformat()}&end={end_date.isoformat()}"
    )
    data = safe_get_json(url)
    return extract_energy_charts_df(data)

# =========================================================
# 3) Energy-Charts – Erneuerbaren-Anteil
# =========================================================
@st.cache_data(ttl=3600)
def get_energy_charts_renewable_share(days=30):
    end_date = datetime.utcnow().date()
    start_date = end_date - timedelta(days=days)

    last_error = None
    for endpoint in ["ren_share", "ren_share_daily_avg"]:
        try:
            url = (
                f"https://api.energy-charts.info/{endpoint}"
                f"?country=de&start={start_date.isoformat()}&end={end_date.isoformat()}"
            )
            data = safe_get_json(url)
            df = extract_energy_charts_df(data)
            return endpoint, df
        except Exception as e:
            last_error = e

    raise RuntimeError(f"Kein passender Energy-Charts-Endpunkt für Erneuerbaren-Anteil gefunden: {last_error}")

# =========================================================
# 4) Energy-Charts – Installierte Leistung (Wind / Solar)
# =========================================================
@st.cache_data(ttl=3600)
def get_energy_charts_installed_power():
    current_year = datetime.utcnow().year
    start_date = f"{current_year}-01-01"
    end_date = datetime.utcnow().date().isoformat()

    url = (
        "https://api.energy-charts.info/installed_power"
        f"?country=de&start={start_date}&end={end_date}"
    )
    data = safe_get_json(url)
    return extract_energy_charts_df(data)

# =========================================================
# 5) SMARD – Haushalts-Gaspreis (monatlich)
# =========================================================
@st.cache_data(ttl=86400)
def get_smard_household_gas_price():
    candidates = [
        ("month", 5190000),
        ("month", 5190001),
        ("month", 5191000),
        ("month", 5191001),
        ("month", 5390000),
        ("month", 5391000),
    ]

    last_error = None
    for resolution, filter_id in candidates:
        try:
            idx_url = f"https://www.smard.de/app/chart_data/{filter_id}/DE/index_{resolution}.json"
            idx = safe_get_json(idx_url)
            timestamps = idx.get("timestamps", [])
            if not timestamps:
                continue

            latest_ts = timestamps[-1]
            data_url = (
                f"https://www.smard.de/app/chart_data/{filter_id}/DE/"
                f"{filter_id}_DE_{resolution}_{latest_ts}.json"
            )
            data = safe_get_json(data_url)
            series = data.get("series", [])
            if not series:
                continue

            df = parse_smard_series(series, "gaspreis_ct_kwh")
            values = pd.to_numeric(df["gaspreis_ct_kwh"], errors="coerce").dropna()

            if not values.empty and values.between(1, 50).mean() > 0.5:
                return df
        except Exception as e:
            last_error = e

    raise RuntimeError(
        f"SMARD-Haushaltsgaspreis konnte nicht geladen werden. "
        f"Letzter Fehler: {last_error}"
    )

# =========================================================
# 6) Tankerkönig – Spritpreis (Super E5, angenäherter Mittelwert)
# =========================================================
@st.cache_data(ttl=1800)
def get_tankerkoenig_daily_price():
    if not TANKERKOENIG_API_KEY:
        return None

    stations = [
        ("Berlin", 52.5200, 13.4050),
        ("Hamburg", 53.5511, 9.9937),
        ("München", 48.1351, 11.5820),
        ("Köln", 50.9375, 6.9603),
        ("Frankfurt", 50.1109, 8.6821),
        ("Leipzig", 51.3397, 12.3731),
    ]

    prices = []
    for _, lat, lng in stations:
        url = (
            "https://creativecommons.tankerkoenig.de/json/list.php"
            f"?lat={lat}&lng={lng}&rad=8&sort=dist&type=all&apikey={TANKERKOENIG_API_KEY}"
        )
        data = safe_get_json(url)
        for station in data.get("stations", []):
            price = station.get("e5")
            if isinstance(price, (int, float)) and price > 0:
                prices.append(price)

    if not prices:
        raise RuntimeError("Tankerkönig lieferte keine verwertbaren E5-Preise.")

    now = pd.Timestamp.now(tz="Europe/Berlin")
    return pd.DataFrame({
        "time": [now],
        "spritpreis_eur_l": [sum(prices) / len(prices)]
    })

# =========================================================
# 7) AGSI – Gasspeicherfüllstand
# =========================================================
@st.cache_data(ttl=3600)
def get_gas_storage():
    if not AGSI_API_KEY:
        return None

    headers = {
        "x-key": AGSI_API_KEY,
        "User-Agent": "Mozilla/5.0",
    }
    url = "https://agsi.gie.eu/api?country=DE"
    data = safe_get_json(url, headers=headers)

    rows = data.get("data", [])
    if not rows:
        raise RuntimeError("AGSI lieferte keine Speicher-Daten.")

    df = pd.DataFrame(rows)
    if "gasDayStart" in df.columns:
        df["time"] = pd.to_datetime(df["gasDayStart"])
    elif "date" in df.columns:
        df["time"] = pd.to_datetime(df["date"])
    else:
        raise RuntimeError("Kein Datumsfeld in AGSI-Daten gefunden.")

    for col in ["full", "gasInStorage", "workingGasVolume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df.sort_values("time")

# =========================================================
# Daten laden
# =========================================================
errors = {}

try:
    df_price = get_smard_day_ahead_price().tail(24 * 30)
except Exception as e:
    df_price = None
    errors["strompreis"] = str(e)

try:
    df_mix = get_energy_charts_total_power(days=7)
except Exception as e:
    df_mix = None
    errors["stromproduktion"] = str(e)

try:
    ren_endpoint, df_ren = get_energy_charts_renewable_share(days=30)
except Exception as e:
    ren_endpoint, df_ren = None, None
    errors["erneuerbare"] = str(e)

try:
    df_installed = get_energy_charts_installed_power()
except Exception as e:
    df_installed = None
    errors["ausbau"] = str(e)

try:
    df_gas = get_smard_household_gas_price()
except Exception as e:
    df_gas = None
    errors["gaspreis"] = str(e)

try:
    df_fuel = get_tankerkoenig_daily_price()
except Exception as e:
    df_fuel = None
    errors["spritpreis"] = str(e)

try:
    df_storage = get_gas_storage()
except Exception as e:
    df_storage = None
    errors["gasspeicher"] = str(e)

# =========================================================
# Kennzahlen oben
# =========================================================
c1, c2, c3, c4 = st.columns(4)

with c1:
    if df_price is not None:
        last = pd.to_numeric(df_price["strompreis_eur_mwh"], errors="coerce").dropna().iloc[-1]
        st.metric("Strompreis", f"{last:.2f} €/MWh")
    else:
        st.metric("Strompreis", "–")

with c2:
    if df_gas is not None:
        last = pd.to_numeric(df_gas["gaspreis_ct_kwh"], errors="coerce").dropna().iloc[-1]
        st.metric("Gaspreis", f"{last:.2f} ct/kWh")
    else:
        st.metric("Gaspreis", "–")

with c3:
    if df_fuel is not None:
        last = pd.to_numeric(df_fuel["spritpreis_eur_l"], errors="coerce").dropna().iloc[-1]
        st.metric("Spritpreis (E5)", f"{last:.3f} €/l")
    else:
        st.metric("Spritpreis (E5)", "API-Key fehlt" if not TANKERKOENIG_API_KEY else "–")

with c4:
    if df_storage is not None and "full" in df_storage.columns:
        last = pd.to_numeric(df_storage["full"], errors="coerce").dropna().iloc[-1]
        st.metric("Gasspeicher", f"{last:.1f} %")
    else:
        st.metric("Gasspeicher", "API-Key fehlt" if not AGSI_API_KEY else "–")

# =========================================================
# Tabs
# =========================================================
tabs = st.tabs([
    "Windkraftausbau",
    "Solarausbau",
    "Erneuerbare",
    "Stromproduktion",
    "Strompreis",
    "Spritpreis",
    "Gaspreis",
    "Füllstand Gasspeicher",
])

# Windkraftausbau
with tabs[0]:
    st.subheader("Windkraftausbau / installierte Leistung")
    if df_installed is not None:
        wind_cols = [
            c for c in df_installed.columns
            if c != "time" and (
                "wind" in c.lower() or
                "onshore" in c.lower() or
                "offshore" in c.lower()
            )
        ]
        if wind_cols:
            df_plot = df_installed[["time"] + wind_cols].copy()
            df_plot["Wind gesamt"] = df_plot[wind_cols].sum(axis=1)
            fig = px.line(df_plot, x="time", y="Wind gesamt")
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(df_plot.tail(30), use_container_width=True)
        else:
            st.info(f"Keine Wind-Serien erkannt. Verfügbare Spalten: {list(df_installed.columns)}")
    else:
        st.error(errors.get("ausbau", "Unbekannter Fehler"))

# Solarausbau
with tabs[1]:
    st.subheader("Solarausbau / installierte Leistung")
    if df_installed is not None:
        solar_cols = [
            c for c in df_installed.columns
            if c != "time" and (
                "solar" in c.lower() or
                "pv" in c.lower() or
                "photovoltaic" in c.lower()
            )
        ]
        if solar_cols:
            df_plot = df_installed[["time"] + solar_cols].copy()
            df_plot["Solar gesamt"] = df_plot[solar_cols].sum(axis=1)
            fig = px.line(df_plot, x="time", y="Solar gesamt")
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(df_plot.tail(30), use_container_width=True)
        else:
            st.info(f"Keine Solar-Serien erkannt. Verfügbare Spalten: {list(df_installed.columns)}")
    else:
        st.error(errors.get("ausbau", "Unbekannter Fehler"))

# Erneuerbare
with tabs[2]:
    st.subheader("Anteil erneuerbarer Energien")
    if df_ren is not None:
        cols = [c for c in df_ren.columns if c != "time"]
        if cols:
            plot_cols = cols[:4]
            df_long = df_ren[["time"] + plot_cols].melt(
                id_vars="time", var_name="Serie", value_name="Wert"
            )
            fig = px.line(df_long, x="time", y="Wert", color="Serie")
            st.plotly_chart(fig, use_container_width=True)
            st.caption(f"Energy-Charts Endpoint: {ren_endpoint}")
            st.dataframe(df_ren.tail(50), use_container_width=True)
        else:
            st.info("Keine Erneuerbaren-Serien erkannt.")
    else:
        st.error(errors.get("erneuerbare", "Unbekannter Fehler"))

# Stromproduktion
with tabs[3]:
    st.subheader("Stromproduktion / Strommix")
    if df_mix is not None:
        cols = [c for c in df_mix.columns if c != "time"]
        wanted = ["Solar", "Wind", "Onshore", "Offshore", "Biomass", "Hydro", "Gas", "Coal", "Lignite"]
        keep = []
        for w in wanted:
            for c in cols:
                if w.lower() in c.lower() and c not in keep:
                    keep.append(c)
        keep = keep[:8]

        if keep:
            df_long = df_mix[["time"] + keep].melt(
                id_vars="time", var_name="Serie", value_name="Wert"
            )
            fig = px.area(df_long, x="time", y="Wert", color="Serie")
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(df_mix.tail(50), use_container_width=True)
        else:
            st.info(f"Keine geeigneten Stromproduktions-Serien erkannt. Verfügbare Spalten: {list(df_mix.columns)}")
    else:
        st.error(errors.get("stromproduktion", "Unbekannter Fehler"))

# Strompreis
with tabs[4]:
    st.subheader("Gewichteter Stromgroßhandelspreis (Day-Ahead)")
    if df_price is not None:
        fig = px.line(df_price, x="time", y="strompreis_eur_mwh")
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(df_price.tail(100), use_container_width=True)
    else:
        st.error(errors.get("strompreis", "Unbekannter Fehler"))

# Spritpreis
with tabs[5]:
    st.subheader("Spritpreis (Super E5)")
    if df_fuel is not None:
        st.metric("Aktueller Mittelwert", f"{df_fuel['spritpreis_eur_l'].iloc[-1]:.3f} €/l")
        st.dataframe(df_fuel, use_container_width=True)
        st.caption("Mittelwert aus mehreren Großstadtabfragen über Tankerkönig.")
    else:
        if not TANKERKOENIG_API_KEY:
            st.info("Bitte TANKERKOENIG_API_KEY in den Streamlit-Secrets hinterlegen.")
        else:
            st.error(errors.get("spritpreis", "Unbekannter Fehler"))

# Gaspreis
with tabs[6]:
    st.subheader("Haushalts-Gaspreis")
    if df_gas is not None:
        fig = px.line(df_gas, x="time", y="gaspreis_ct_kwh")
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(df_gas.tail(50), use_container_width=True)
        st.caption("Monatliche SMARD-Haushaltskundenpreise.")
    else:
        st.error(errors.get("gaspreis", "Unbekannter Fehler"))

# Gasspeicher
with tabs[7]:
    st.subheader("Füllstand Gasspeicher")
    if df_storage is not None:
        candidate = "full" if "full" in df_storage.columns else None
        if candidate:
            fig = px.line(df_storage, x="time", y=candidate)
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(df_storage.tail(50), use_container_width=True)
        else:
            st.info(f"Keine Füllstands-Spalte erkannt. Verfügbare Spalten: {list(df_storage.columns)}")
    else:
        if not AGSI_API_KEY:
            st.info("Bitte AGSI_API_KEY in den Streamlit-Secrets hinterlegen.")
        else:
            st.error(errors.get("gasspeicher", "Unbekannter Fehler"))
