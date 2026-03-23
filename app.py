import streamlit as st
import requests
import pandas as pd

st.set_page_config(layout="wide")
st.title("SMARD Debug")

HEADERS = {"User-Agent": "Mozilla/5.0"}

st.write("1. App gestartet")

try:
    filter_id = 4169
    region = "DE"
    resolution = "hour"

    idx_url = f"https://www.smard.de/app/chart_data/{filter_id}/{region}/index_{resolution}.json"
    st.write("2. Index-URL:", idx_url)

    idx_response = requests.get(idx_url, headers=HEADERS, timeout=30)
    st.write("3. Index-Status:", idx_response.status_code)

    idx_response.raise_for_status()

    idx_data = idx_response.json()
    st.write("4. Index-JSON geladen")
    st.write("5. Keys im Index:", list(idx_data.keys()))

    timestamps = idx_data.get("timestamps", [])
    st.write("6. Anzahl Timestamps:", len(timestamps))

    if not timestamps:
        st.error("Keine Timestamps gefunden.")
        st.stop()

    latest_ts = timestamps[-1]
    st.write("7. Letzter Timestamp:", latest_ts)

    data_url = (
        f"https://www.smard.de/app/chart_data/{filter_id}/{region}/"
        f"{filter_id}_{region}_{resolution}_{latest_ts}.json"
    )
    st.write("8. Daten-URL:", data_url)

    data_response = requests.get(data_url, headers=HEADERS, timeout=30)
    st.write("9. Daten-Status:", data_response.status_code)

    data_response.raise_for_status()

    data = data_response.json()
    st.write("10. Daten-JSON geladen")
    st.write("11. Keys in Daten:", list(data.keys()))

    series = data.get("series", [])
    st.write("12. Anzahl Serienpunkte:", len(series))

    if not series:
        st.error("Keine Serienwerte gefunden.")
        st.stop()

    df = pd.DataFrame(series, columns=["timestamp", "preis"])
    st.write("13. DataFrame erstellt")
    st.dataframe(df.head())

    df["time"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.tz_convert("Europe/Berlin")
    df["preis"] = pd.to_numeric(df["preis"], errors="coerce")

    st.write("14. Zeit umgewandelt")
    st.metric("Letzter Preis", f"{df['preis'].dropna().iloc[-1]:.2f} €/MWh")

    st.line_chart(df.set_index("time")["preis"])

except Exception as e:
    st.error(f"Fehler: {type(e).__name__}: {e}")
