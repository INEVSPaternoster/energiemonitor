import streamlit as st
import pandas as pd
import requests
import plotly.express as px
from datetime import datetime, timedelta

st.set_page_config(layout="wide")
st.title("Energiemonitor Deutschland")

# =========================================================
# Konfiguration
# =========================================================
SMARD_FILTER_ID = 4169   # gewichteter Day-Ahead-Großhandelspreis
SMARD_REGION = "DE"
SMARD_RESOLUTION = "hour"

EC_COUNTRY = "de"

# Für AGSI brauchst du meist einen API-Key.
# Wenn du noch keinen hast, lass das Feld leer.
AGSI_API_KEY = st.secrets.get("AGSI_API_KEY", "")

# =========================================================
# Hilfsfunktionen
# =========================================================
def safe_request_json(url, headers=None, timeout=30):
    r = requests.get(url, headers=headers or {}, timeout=timeout)
    r.raise_for_status()
    return r.json()

def find_datetime_column(df):
    for c in df.columns:
        cl = c.lower()
        if "time" in cl or "date" in cl or "timestamp" in cl:
            return c
    return None

def numeric_columns(df):
    return [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]

# =========================================================
# 1) SMARD – Strompreis
# =========================================================
@st.cache_data(ttl=3600)
def get_smard_price():
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    index_url = (
        f"https://www.smard.de/app/chart_data/"
        f"{SMARD_FILTER_ID}/{SMARD_REGION}/index_{SMARD_RESOLUTION}.json"
    )
    index_data = session.get(index_url, timeout=30).json()
    timestamps = index_data.get("timestamps", [])
    if not timestamps:
        raise RuntimeError("SMARD: keine Timestamps gefunden.")

    latest_ts = timestamps[-1]
    data_url = (
        f"https://www.smard.de/app/chart_data/"
        f"{SMARD_FILTER_ID}/{SMARD_REGION}/"
        f"{SMARD_FILTER_ID}_{SMARD_REGION}_{SMARD_RESOLUTION}_{latest_ts}.json"
    )
    data = session.get(data_url, timeout=30).json()

    series = data.get("series", [])
    if not series:
        raise RuntimeError("SMARD: keine Zeitreihendaten gefunden.")

    df = pd.DataFrame(series, columns=["timestamp", "price_eur_per_mwh"])
    df["time"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.tz_convert("Europe/Berlin")
    return df.drop(columns=["timestamp"]).sort_values("time")

# =========================================================
# 2) Energy Charts – öffentliche Stromerzeugung / Strommix
# =========================================================
@st.cache_data(ttl=3600)
def get_energy_charts_total_power(days=7):
    end_date = datetime.utcnow().date()
    start_date = end_date - timedelta(days=days)

    url = (
        "https://api.energy-charts.info/total_power"
        f"?country={EC_COUNTRY}&start={start_date.isoformat()}&end={end_date.isoformat()}"
    )
    data = safe_request_json(url)

    production = data.get("production_types", [])
    unix_seconds = data.get("unix_seconds", [])

    if not production or not unix_seconds:
        raise RuntimeError("Energy-Charts: keine total_power-Daten gefunden.")

    df = pd.DataFrame({"time": pd.to_datetime(unix_seconds, unit="s", utc=True).tz_convert("Europe/Berlin")})

    for item in production:
        name = item.get("name", "Unbekannt")
        values = item.get("data", [])
        if len(values) == len(df):
            df[name] = values

    return df.sort_values("time")

# =========================================================
# 3) Energy Charts – Erneuerbaren-Anteil
# =========================================================
@st.cache_data(ttl=3600)
def get_energy_charts_renewable_share(days=30):
    end_date = datetime.utcnow().date()
    start_date = end_date - timedelta(days=days)

    # Endpoint je nach API-Version
    for endpoint in ["ren_share", "ren_share_daily_avg"]:
        try:
            url = (
                f"https://api.energy-charts.info/{endpoint}"
                f"?country={EC_COUNTRY}&start={start_date.isoformat()}&end={end_date.isoformat()}"
            )
            data = safe_request_json(url)

            # mögliche Formate abfangen
            if "unix_seconds" in data:
                df = pd.DataFrame({
                    "time": pd.to_datetime(data["unix_seconds"], unit="s", utc=True).tz_convert("Europe/Berlin")
                })
                for k, v in data.items():
                    if k == "unix_seconds":
                        continue
                    if isinstance(v, list) and len(v) == len(df):
                        df[k] = v
                return endpoint, df

        except Exception:
            pass

    raise RuntimeError("Energy-Charts: Erneuerbaren-Anteil konnte nicht geladen werden.")

# =========================================================
# 4) Energy Charts – installierte Leistung
# =========================================================
@st.cache_data(ttl=3600)
def get_energy_charts_installed_power():
    current_year = datetime.utcnow().year
    start_date = f"{current_year}-01-01"
    end_date = datetime.utcnow().date().isoformat()

    url = (
        "https://api.energy-charts.info/installed_power"
        f"?country={EC_COUNTRY}&start={start_date}&end={end_date}"
    )
    data = safe_request_json(url)

    power_types = data.get("production_types", [])
    unix_seconds = data.get("unix_seconds", [])

    if not power_types or not unix_seconds:
        raise RuntimeError("Energy-Charts: keine installed_power-Daten gefunden.")

    df = pd.DataFrame({"time": pd.to_datetime(unix_seconds, unit="s", utc=True).tz_convert("Europe/Berlin")})

    for item in power_types:
        name = item.get("name", "Unbekannt")
        values = item.get("data", [])
        if len(values) == len(df):
            df[name] = values

    return df.sort_values("time")

# =========================================================
# 5) AGSI – Gasspeicherfüllstand
# =========================================================
@st.cache_data(ttl=3600)
def get_gas_storage():
    if not AGSI_API_KEY:
        return None

    headers = {
        "x-key": AGSI_API_KEY,
        "User-Agent": "Mozilla/5.0",
    }

    # Deutschland gesamt
    url = "https://agsi.gie.eu/api?country=DE"
    data = safe_request_json(url, headers=headers)

    records = data.get("data", [])
    if not records:
        raise RuntimeError("AGSI: keine Speicher-Daten gefunden.")

    df = pd.DataFrame(records)
    if "gasDayStart" in df.columns:
        df["time"] = pd.to_datetime(df["gasDayStart"])
    elif "date" in df.columns:
        df["time"] = pd.to_datetime(df["date"])
    else:
        raise RuntimeError("AGSI: kein Datumsfeld gefunden.")

    # häufig ist "full" oder "gasInStorage"
    for candidate in ["full", "gasInStorage", "workingGasVolume"]:
        if candidate in df.columns:
            df[candidate] = pd.to_numeric(df[candidate], errors="coerce")

    return df.sort_values("time")

# =========================================================
# Daten laden
# =========================================================
errors = {}

try:
    df_price = get_smard_price().tail(24 * 30)
except Exception as e:
    df_price = None
    errors["price"] = str(e)

try:
    df_mix = get_energy_charts_total_power(days=7)
except Exception as e:
    df_mix = None
    errors["mix"] = str(e)

try:
    ren_endpoint, df_ren = get_energy_charts_renewable_share(days=30)
except Exception as e:
    ren_endpoint, df_ren = None, None
    errors["renewables"] = str(e)

try:
    df_installed = get_energy_charts_installed_power()
except Exception as e:
    df_installed = None
    errors["installed"] = str(e)

try:
    df_storage = get_gas_storage()
except Exception as e:
    df_storage = None
    errors["storage"] = str(e)

# =========================================================
# Kennzahlen oben
# =========================================================
k1, k2, k3, k4, k5 = st.columns(5)

with k1:
    if df_price is not None:
        st.metric("Strompreis", f"{df_price['price_eur_per_mwh'].dropna().iloc[-1]:.2f} €/MWh")
    else:
        st.metric("Strompreis", "–")

with k2:
    if df_ren is not None:
        value_cols = [c for c in df_ren.columns if c != "time"]
        if value_cols:
            latest_val = pd.to_numeric(df_ren[value_cols[0]], errors="coerce").dropna().iloc[-1]
            st.metric("Erneuerbaren-Anteil", f"{latest_val:.1f}")
        else:
            st.metric("Erneuerbaren-Anteil", "–")
    else:
        st.metric("Erneuerbaren-Anteil", "–")

with k3:
    if df_storage is not None:
        candidate = None
        for c in ["full", "gasInStorage"]:
            if c in df_storage.columns:
                candidate = c
                break
        if candidate:
            latest_val = pd.to_numeric(df_storage[candidate], errors="coerce").dropna().iloc[-1]
            unit = "%" if candidate == "full" else ""
            st.metric("Gasspeicher", f"{latest_val:.1f}{unit}")
        else:
            st.metric("Gasspeicher", "–")
    else:
        st.metric("Gasspeicher", "API-Key fehlt" if not AGSI_API_KEY else "–")

with k4:
    if df_installed is not None:
        wind_cols = [c for c in df_installed.columns if "wind" in c.lower()]
        if wind_cols:
            latest_val = df_installed[wind_cols].sum(axis=1).iloc[-1] / 1000
            st.metric("Installierte Windleistung", f"{latest_val:.1f} GW")
        else:
            st.metric("Installierte Windleistung", "–")
    else:
        st.metric("Installierte Windleistung", "–")

with k5:
    if df_installed is not None:
        solar_cols = [c for c in df_installed.columns if "solar" in c.lower() or "pv" in c.lower()]
        if solar_cols:
            latest_val = df_installed[solar_cols].sum(axis=1).iloc[-1] / 1000
            st.metric("Installierte Solarleistung", f"{latest_val:.1f} GW")
        else:
            st.metric("Installierte Solarleistung", "–")
    else:
        st.metric("Installierte Solarleistung", "–")

# =========================================================
# Tabs
# =========================================================
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "Strompreis",
    "Strommix",
    "Erneuerbare",
    "Ausbau",
    "Gasspeicher"
])

with tab1:
    st.subheader("Gewichteter Stromgroßhandelspreis (Day-Ahead)")
    if df_price is not None:
        fig = px.line(df_price, x="time", y="price_eur_per_mwh", labels={"time": "Zeit", "price_eur_per_mwh": "€/MWh"})
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.error(errors.get("price", "Unbekannter Fehler"))

with tab2:
    st.subheader("Strommix")
    if df_mix is not None:
        cols = [c for c in df_mix.columns if c != "time"]
        # nur häufig relevante Serien
        keep = []
        wanted = ["Solar", "Wind", "Onshore", "Offshore", "Biomass", "Hydro", "Lignite", "Coal", "Gas", "Nuclear"]
        for w in wanted:
            for c in cols:
                if w.lower() in c.lower() and c not in keep:
                    keep.append(c)
        keep = keep[:8]

        if keep:
            df_long = df_mix[["time"] + keep].melt(id_vars="time", var_name="Serie", value_name="Wert")
            fig = px.area(df_long, x="time", y="Wert", color="Serie")
            st.plotly_chart(fig, use_container_width=True)
        st.dataframe(df_mix.tail(50), use_container_width=True)
    else:
        st.error(errors.get("mix", "Unbekannter Fehler"))

with tab3:
    st.subheader("Anteil erneuerbarer Energien")
    if df_ren is not None:
        cols = [c for c in df_ren.columns if c != "time"]
        if cols:
            df_long = df_ren[["time"] + cols[:4]].melt(id_vars="time", var_name="Serie", value_name="Wert")
            fig = px.line(df_long, x="time", y="Wert", color="Serie")
            st.plotly_chart(fig, use_container_width=True)
        st.caption(f"Quelle: Energy-Charts Endpoint {ren_endpoint}")
        st.dataframe(df_ren.tail(50), use_container_width=True)
    else:
        st.error(errors.get("renewables", "Unbekannter Fehler"))

with tab4:
    st.subheader("Installierte Leistung / Ausbau")
    if df_installed is not None:
        cols = [c for c in df_installed.columns if c != "time"]
        keep = []
        for w in ["Wind", "Solar", "PV"]:
            for c in cols:
                if w.lower() in c.lower() and c not in keep:
                    keep.append(c)
        keep = keep[:6]

        if keep:
            df_long = df_installed[["time"] + keep].melt(id_vars="time", var_name="Serie", value_name="Wert")
            fig = px.line(df_long, x="time", y="Wert", color="Serie")
            st.plotly_chart(fig, use_container_width=True)
        st.dataframe(df_installed.tail(50), use_container_width=True)
    else:
        st.error(errors.get("installed", "Unbekannter Fehler"))

with tab5:
    st.subheader("Gasspeicher-Füllstand")
    if df_storage is not None:
        candidate = None
        for c in ["full", "gasInStorage"]:
            if c in df_storage.columns:
                candidate = c
                break

        if candidate:
            fig = px.line(df_storage, x="time", y=candidate, labels={"time": "Zeit", candidate: candidate})
            st.plotly_chart(fig, use_container_width=True)
        st.dataframe(df_storage.tail(50), use_container_width=True)
    else:
        if not AGSI_API_KEY:
            st.info("Für den Gasspeicher fehlt noch ein AGSI-API-Key in den Streamlit-Secrets.")
        else:
            st.error(errors.get("storage", "Unbekannter Fehler"))
