import streamlit as st
import pandas as pd
import requests
import plotly.express as px
from datetime import datetime, timedelta

st.set_page_config(layout="wide")
st.title("Energiemonitor Deutschland")

AGSI_API_KEY = st.secrets.get("AGSI_API_KEY", "")
TANKERKOENIG_API_KEY = st.secrets.get("TANKERKOENIG_API_KEY", "")

HEADERS = {"User-Agent": "Mozilla/5.0"}

# -------------------------------------------------
# Helpers
# -------------------------------------------------
def get_json(url, headers=None, timeout=30):
    r = requests.get(url, headers=headers or HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()

def to_berlin_time(series, unit="ms"):
    return pd.to_datetime(series, unit=unit, utc=True).dt.tz_convert("Europe/Berlin")

def safe_number(x):
    return pd.to_numeric(x, errors="coerce")

# -------------------------------------------------
# SMARD: Strompreis
# -------------------------------------------------
@st.cache_data(ttl=3600)
def load_smard_strompreis():
    filter_id = 4169
    region = "DE"
    resolution = "hour"

    idx_url = f"https://www.smard.de/app/chart_data/{filter_id}/{region}/index_{resolution}.json"
    idx = get_json(idx_url)
    timestamps = idx.get("timestamps", [])
    if not timestamps:
        raise RuntimeError("SMARD: keine Timestamps gefunden.")

    latest_ts = timestamps[-1]
    data_url = (
        f"https://www.smard.de/app/chart_data/{filter_id}/{region}/"
        f"{filter_id}_{region}_{resolution}_{latest_ts}.json"
    )
    data = get_json(data_url)

    series = data.get("series", [])
    if not series:
        raise RuntimeError("SMARD: keine Zeitreihe gefunden.")

    df = pd.DataFrame(series, columns=["timestamp", "strompreis_eur_mwh"])
    df["time"] = to_berlin_time(df["timestamp"], unit="ms")
    df["strompreis_eur_mwh"] = safe_number(df["strompreis_eur_mwh"])
    return df.drop(columns=["timestamp"]).sort_values("time")

# -------------------------------------------------
# Energy Charts: total_power
# -------------------------------------------------
@st.cache_data(ttl=3600)
def load_total_power(days=7):
    end_date = datetime.utcnow().date()
    start_date = end_date - timedelta(days=days)

    url = (
        "https://api.energy-charts.info/total_power"
        f"?country=de&start={start_date.isoformat()}&end={end_date.isoformat()}"
    )
    data = get_json(url)

    unix_seconds = data.get("unix_seconds", [])
    production = data.get("production_types", [])

    if not unix_seconds or not production:
        raise RuntimeError("Energy-Charts: total_power leer oder Format unerwartet.")

    df = pd.DataFrame({
        "time": pd.to_datetime(unix_seconds, unit="s", utc=True).tz_convert("Europe/Berlin")
    })

    for item in production:
        name = item.get("name", "Unbekannt")
        values = item.get("data", [])
        if len(values) == len(df):
            df[name] = safe_number(values)

    if len(df.columns) <= 1:
        raise RuntimeError("Energy-Charts: keine Produktionsspalten erkannt.")

    return df.sort_values("time")

# -------------------------------------------------
# Energy Charts: installed_power
# -------------------------------------------------
@st.cache_data(ttl=3600)
def load_installed_power():
    year = datetime.utcnow().year
    start_date = f"{year}-01-01"
    end_date = datetime.utcnow().date().isoformat()

    url = (
        "https://api.energy-charts.info/installed_power"
        f"?country=de&start={start_date}&end={end_date}"
    )
    data = get_json(url)

    unix_seconds = data.get("unix_seconds", [])
    production = data.get("production_types", [])

    if not unix_seconds or not production:
        raise RuntimeError("Energy-Charts: installed_power leer oder Format unerwartet.")

    df = pd.DataFrame({
        "time": pd.to_datetime(unix_seconds, unit="s", utc=True).tz_convert("Europe/Berlin")
    })

    for item in production:
        name = item.get("name", "Unbekannt")
        values = item.get("data", [])
        if len(values) == len(df):
            df[name] = safe_number(values)

    if len(df.columns) <= 1:
        raise RuntimeError("Energy-Charts: keine Ausbau-Spalten erkannt.")

    return df.sort_values("time")

# -------------------------------------------------
# Energy Charts: erneuerbare aus total_power ableiten
# -------------------------------------------------
def add_renewable_share(df):
    renewable_keywords = ["solar", "pv", "wind", "hydro", "biomass", "renewable", "geothermal"]
    all_cols = [c for c in df.columns if c != "time"]

    renewable_cols = [
        c for c in all_cols
        if any(k in c.lower() for k in renewable_keywords)
    ]

    if not renewable_cols:
        raise RuntimeError(f"Keine erneuerbaren Spalten erkannt. Vorhanden: {all_cols}")

    df2 = df.copy()
    df2["gesamt"] = df2[all_cols].sum(axis=1, skipna=True)
    df2["erneuerbare"] = df2[renewable_cols].sum(axis=1, skipna=True)
    df2["erneuerbaren_anteil_prozent"] = (df2["erneuerbare"] / df2["gesamt"]) * 100
    return df2, renewable_cols

# -------------------------------------------------
# Gaspreis: Platzhalter mit sauberer Meldung
# -------------------------------------------------
@st.cache_data(ttl=86400)
def load_gaspreis_placeholder():
    raise RuntimeError(
        "Gaspreis ist noch nicht stabil angebunden. "
        "Diesen Block setzen wir im nächsten Schritt separat sauber auf."
    )

# -------------------------------------------------
# Tankerkönig: Spritpreis
# -------------------------------------------------
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
        data = get_json(url)
        if not data.get("ok", False):
            continue
        for station in data.get("stations", []):
            price = station.get("e5")
            if isinstance(price, (int, float)) and price > 0:
                prices.append(price)

    if not prices:
        raise RuntimeError("Tankerkönig: keine E5-Preise gefunden.")

    return pd.DataFrame({
        "time": [pd.Timestamp.now(tz="Europe/Berlin")],
        "spritpreis_eur_l": [sum(prices) / len(prices)]
    })

# -------------------------------------------------
# AGSI: Gasspeicher
# -------------------------------------------------
@st.cache_data(ttl=3600)
def load_gasspeicher():
    if not AGSI_API_KEY:
        return None

    headers = {
        "x-key": AGSI_API_KEY,
        "User-Agent": "Mozilla/5.0",
    }
    data = get_json("https://agsi.gie.eu/api?country=DE", headers=headers)

    rows = data.get("data", [])
    if not rows:
        raise RuntimeError("AGSI: keine Daten erhalten.")

    df = pd.DataFrame(rows)
    if "gasDayStart" in df.columns:
        df["time"] = pd.to_datetime(df["gasDayStart"])
    elif "date" in df.columns:
        df["time"] = pd.to_datetime(df["date"])
    else:
        raise RuntimeError(f"AGSI: kein Datumsfeld erkannt. Spalten: {list(df.columns)}")

    if "full" in df.columns:
        df["full"] = safe_number(df["full"])

    return df.sort_values("time")

# -------------------------------------------------
# Daten laden
# -------------------------------------------------
errors = {}

try:
    df_price = load_smard_strompreis().tail(24 * 30)
except Exception as e:
    df_price = None
    errors["strompreis"] = str(e)

try:
    df_total = load_total_power(days=7)
except Exception as e:
    df_total = None
    errors["stromproduktion"] = str(e)

try:
    df_installed = load_installed_power()
except Exception as e:
    df_installed = None
    errors["ausbau"] = str(e)

try:
    if df_total is not None:
        df_ren, ren_cols = add_renewable_share(df_total)
    else:
        df_ren, ren_cols = None, []
except Exception as e:
    df_ren = None
    ren_cols = []
    errors["erneuerbare"] = str(e)

try:
    df_gas = load_gaspreis_placeholder()
except Exception as e:
    df_gas = None
    errors["gaspreis"] = str(e)

try:
    df_fuel = load_spritpreis()
except Exception as e:
    df_fuel = None
    errors["spritpreis"] = str(e)

try:
    df_storage = load_gasspeicher()
except Exception as e:
    df_storage = None
    errors["gasspeicher"] = str(e)

# -------------------------------------------------
# Top metrics
# -------------------------------------------------
c1, c2, c3, c4 = st.columns(4)

with c1:
    if df_price is not None:
        last = df_price["strompreis_eur_mwh"].dropna().iloc[-1]
        st.metric("Strompreis", f"{last:.2f} €/MWh")
    else:
        st.metric("Strompreis", "–")

with c2:
    if df_ren is not None:
        last = df_ren["erneuerbaren_anteil_prozent"].dropna().iloc[-1]
        st.metric("Erneuerbare", f"{last:.1f} %")
    else:
        st.metric("Erneuerbare", "–")

with c3:
    if df_fuel is not None:
        last = df_fuel["spritpreis_eur_l"].dropna().iloc[-1]
        st.metric("Spritpreis (E5)", f"{last:.3f} €/l")
    else:
        st.metric("Spritpreis (E5)", "API-Key fehlt" if not TANKERKOENIG_API_KEY else "–")

with c4:
    if df_storage is not None and "full" in df_storage.columns:
        last = df_storage["full"].dropna().iloc[-1]
        st.metric("Gasspeicher", f"{last:.1f} %")
    else:
        st.metric("Gasspeicher", "API-Key fehlt" if not AGSI_API_KEY else "–")

# -------------------------------------------------
# Tabs
# -------------------------------------------------
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

with tabs[0]:
    st.subheader("Windkraftausbau / installierte Leistung")
    if df_installed is not None:
        wind_cols = [c for c in df_installed.columns if c != "time" and "wind" in c.lower()]
        if wind_cols:
            df_plot = df_installed[["time"] + wind_cols].copy()
            df_plot["Wind gesamt"] = df_plot[wind_cols].sum(axis=1)
            fig = px.line(df_plot, x="time", y="Wind gesamt")
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(df_plot.tail(30), use_container_width=True)
        else:
            st.warning(f"Keine Wind-Spalten erkannt. Vorhanden: {list(df_installed.columns)}")
    else:
        st.error(errors.get("ausbau", "Unbekannter Fehler"))

with tabs[1]:
    st.subheader("Solarausbau / installierte Leistung")
    if df_installed is not None:
        solar_cols = [c for c in df_installed.columns if c != "time" and ("solar" in c.lower() or "pv" in c.lower())]
        if solar_cols:
            df_plot = df_installed[["time"] + solar_cols].copy()
            df_plot["Solar gesamt"] = df_plot[solar_cols].sum(axis=1)
            fig = px.line(df_plot, x="time", y="Solar gesamt")
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(df_plot.tail(30), use_container_width=True)
        else:
            st.warning(f"Keine Solar-Spalten erkannt. Vorhanden: {list(df_installed.columns)}")
    else:
        st.error(errors.get("ausbau", "Unbekannter Fehler"))

with tabs[2]:
    st.subheader("Anteil erneuerbarer Energien")
    if df_ren is not None:
        fig = px.line(df_ren, x="time", y="erneuerbaren_anteil_prozent")
        st.plotly_chart(fig, use_container_width=True)
        st.caption(f"Erkannte erneuerbare Spalten: {ren_cols}")
        st.dataframe(df_ren[["time", "erneuerbaren_anteil_prozent"]].tail(50), use_container_width=True)
    else:
        st.error(errors.get("erneuerbare", "Unbekannter Fehler"))

with tabs[3]:
    st.subheader("Stromproduktion / Strommix")
    if df_total is not None:
        cols = [c for c in df_total.columns if c != "time"]
        keep = []
        wanted = ["solar", "wind", "offshore", "onshore", "hydro", "biomass", "gas", "coal", "lignite"]
        for c in cols:
            if any(w in c.lower() for w in wanted):
                keep.append(c)
        keep = keep[:8]

        if keep:
            df_long = df_total[["time"] + keep].melt(id_vars="time", var_name="Serie", value_name="Wert")
            fig = px.area(df_long, x="time", y="Wert", color="Serie")
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(df_total.tail(50), use_container_width=True)
        else:
            st.warning(f"Keine passenden Produktionsspalten erkannt. Vorhanden: {cols}")
    else:
        st.error(errors.get("stromproduktion", "Unbekannter Fehler"))

with tabs[4]:
    st.subheader("Gewichteter Stromgroßhandelspreis (Day-Ahead)")
    if df_price is not None:
        fig = px.line(df_price, x="time", y="strompreis_eur_mwh")
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(df_price.tail(100), use_container_width=True)
    else:
        st.error(errors.get("strompreis", "Unbekannter Fehler"))

with tabs[5]:
    st.subheader("Spritpreis (Super E5)")
    if df_fuel is not None:
        st.metric("Aktueller Mittelwert", f"{df_fuel['spritpreis_eur_l'].iloc[-1]:.3f} €/l")
        st.dataframe(df_fuel, use_container_width=True)
    else:
        if not TANKERKOENIG_API_KEY:
            st.info("Bitte in Streamlit unter Settings → Secrets einen TANKERKOENIG_API_KEY hinterlegen.")
        else:
            st.error(errors.get("spritpreis", "Unbekannter Fehler"))

with tabs[6]:
    st.subheader("Gaspreis")
    if df_gas is not None:
        st.dataframe(df_gas, use_container_width=True)
    else:
        st.warning(errors.get("gaspreis", "Gaspreis noch nicht eingebunden."))

with tabs[7]:
    st.subheader("Füllstand Gasspeicher")
    if df_storage is not None and "full" in df_storage.columns:
        fig = px.line(df_storage, x="time", y="full")
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(df_storage.tail(50), use_container_width=True)
    else:
        if not AGSI_API_KEY:
            st.info("Bitte in Streamlit unter Settings → Secrets einen AGSI_API_KEY hinterlegen.")
        else:
            st.error(errors.get("gasspeicher", "Unbekannter Fehler"))
