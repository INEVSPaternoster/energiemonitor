import streamlit as st
import pandas as pd
import requests
import plotly.express as px

st.set_page_config(layout="wide")
st.title("Energiemonitor – Stromgroßhandelspreis (SMARD)")

FILTER_ID = 4169   # Day-Ahead-Großhandelspreis
REGION = "DE"
RESOLUTION = "hour"  # stabil; quarterhour ist erst seit 01.10.2025 verfügbar

@st.cache_data(ttl=3600)
def get_smard_series(filter_id=FILTER_ID, region=REGION, resolution=RESOLUTION):
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    # 1) Verfügbare Timestamps holen
    index_url = f"https://www.smard.de/app/chart_data/{filter_id}/{region}/index_{resolution}.json"
    r = session.get(index_url, timeout=30)
    r.raise_for_status()

    try:
        index_data = r.json()
    except Exception:
        raise RuntimeError(f"Index-URL liefert kein JSON: {index_url}")

    timestamps = index_data.get("timestamps", [])
    if not timestamps:
        raise RuntimeError("Keine Timestamps von SMARD erhalten.")

    # 2) Neuesten Timestamp verwenden
    latest_ts = timestamps[-1]

    data_url = (
        f"https://www.smard.de/app/chart_data/"
        f"{filter_id}/{region}/{filter_id}_{region}_{resolution}_{latest_ts}.json"
    )
    r = session.get(data_url, timeout=30)
    r.raise_for_status()

    try:
        data = r.json()
    except Exception:
        raise RuntimeError(f"Daten-URL liefert kein JSON: {data_url}")

    series = data.get("series", [])
    if not series:
        raise RuntimeError("Keine Zeitreihendaten von SMARD erhalten.")

    df = pd.DataFrame(series, columns=["timestamp", "price_eur_per_mwh"])
    df["time"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.tz_convert("Europe/Berlin")
    df = df.drop(columns=["timestamp"]).sort_values("time")

    return df

try:
    df = get_smard_series()

    # Letzte 30 Tage anzeigen
    df_plot = df.tail(24 * 30)

    st.metric(
        "Letzter verfügbarer Preis",
        f"{df_plot['price_eur_per_mwh'].iloc[-1]:.2f} €/MWh"
    )

    fig = px.line(
        df_plot,
        x="time",
        y="price_eur_per_mwh",
        title="Gewichteter Stromgroßhandelspreis (Day-Ahead) – Deutschland",
        labels={"time": "Zeit", "price_eur_per_mwh": "€/MWh"},
    )
    st.plotly_chart(fig, use_container_width=True)

    st.dataframe(df_plot.tail(100), use_container_width=True)

except Exception as e:
    st.error(f"Fehler beim Laden der SMARD-Daten: {e}")
