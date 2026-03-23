import streamlit as st
import pandas as pd
import requests
import plotly.express as px

st.set_page_config(layout="wide")
st.title("Energiemonitor – Strompreis")

HEADERS = {"User-Agent": "Mozilla/5.0"}

# -------------------------------------------------
# SMARD sauber gekapselt
# -------------------------------------------------
@st.cache_data(ttl=3600)
def load_smard():
    filter_id = 4169
    region = "DE"
    resolution = "hour"

    idx_url = f"https://www.smard.de/app/chart_data/{filter_id}/{region}/index_{resolution}.json"
    idx = requests.get(idx_url, headers=HEADERS, timeout=30)
    idx.raise_for_status()
    idx_data = idx.json()

    timestamps = idx_data.get("timestamps", [])
    if not timestamps:
        raise RuntimeError("Keine Timestamps gefunden")

    latest_ts = timestamps[-1]

    data_url = (
        f"https://www.smard.de/app/chart_data/{filter_id}/{region}/"
        f"{filter_id}_{region}_{resolution}_{latest_ts}.json"
    )

    data = requests.get(data_url, headers=HEADERS, timeout=30)
    data.raise_for_status()
    data = data.json()

    series = data.get("series", [])
    if not series:
        raise RuntimeError("Keine Zeitreihe gefunden")

    df = pd.DataFrame(series, columns=["timestamp", "preis"])
    df["time"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.tz_convert("Europe/Berlin")
    df["preis"] = pd.to_numeric(df["preis"], errors="coerce")

    return df.dropna()

# -------------------------------------------------
# App
# -------------------------------------------------
try:
    df = load_smard()

    # KPI
    last = df["preis"].iloc[-1]
    st.metric("Aktueller Strompreis", f"{last:.2f} €/MWh")

    # Chart
    fig = px.line(df.tail(24 * 7), x="time", y="preis", title="Strompreis – letzte 7 Tage")
    st.plotly_chart(fig, use_container_width=True)

    # Tabelle
    st.dataframe(df.tail(50), use_container_width=True)

except Exception as e:
    st.error(f"Fehler: {e}")
