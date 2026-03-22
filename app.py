import re
from datetime import datetime, timedelta

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

st.set_page_config(layout="wide", page_title="Energiemonitor Deutschland")
st.title("Energiemonitor Deutschland")

# =========================================================
# Konfiguration
# =========================================================
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; Energiemonitor/1.0)"
}

AGSI_API_KEY = st.secrets.get("AGSI_API_KEY", "")
TANKERKOENIG_API_KEY = st.secrets.get("TANKERKOENIG_API_KEY", "")

ZEIT_URL = "https://www.zeit.de/wirtschaft/energiemonitor-strompreis-gaspreis-erneuerbare-energien-ausbau"

# =========================================================
# Hilfsfunktionen
# =========================================================
def safe_get(url, headers=None, timeout=30):
    r = requests.get(url, headers=headers or HEADERS, timeout=timeout)
    r.raise_for_status()
    return r

def safe_get_json(url, headers=None, timeout=30):
    return safe_get(url, headers=headers, timeout=timeout).json()

def to_berlin_time(series, unit="ms"):
    return pd.to_datetime(series, unit=unit, utc=True).dt.tz_convert("Europe/Berlin")

def to_berlin_time_s(series):
    return pd.to_datetime(series, unit="s", utc=True).dt.tz_convert("Europe/Berlin")

def as_numeric(series):
    return pd.to_numeric(series, errors="coerce")

def df_timeframe_label(df, time_col="time"):
    if df is None or df.empty or time_col not in df.columns:
        return "keine Daten"
    last = pd.to_datetime(df[time_col]).max()
    return last.strftime("%d.%m.%Y %H:%M")

# =========================================================
# ZEIT-Snapshot (aktuelle Verbraucherpreise als Fallback / Snapshot)
# =========================================================
@st.cache_data(ttl=3600)
def load_zeit_snapshot():
    """
    Liest die serverseitig ausgelieferte ZEIT-Seite und versucht,
    aktuelle Textwerte für Gas/Strom/Sprit herauszuziehen.
    Das ist absichtlich nur ein Snapshot, keine vollständige Zeitreihe.
    """
    html = safe_get(ZEIT_URL).text

    # HTML bereinigen
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)

    result = {
        "gas_ct_kwh": None,
        "strom_ct_kwh": None,
        "sprit_eur_l": None,
        "fetched_at": pd.Timestamp.now(tz="Europe/Berlin")
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
# SMARD – Strompreis Day-Ahead
# =========================================================
@st.cache_data(ttl=3600)
def load_smard_strompreis():
    filter_id = 4169
    region = "DE"
    resolution = "hour"

    idx_url = f"https://www.smard.de/app/chart_data/{filter_id}/{region}/index_{resolution}.json"
    idx = safe_get_json(idx_url)
    timestamps = idx.get("timestamps", [])
    if not timestamps:
        raise RuntimeError("SMARD: keine Timestamps für Strompreis gefunden.")

    latest_ts = timestamps[-1]
    data_url = (
        f"https://www.smard.de/app/chart_data/{filter_id}/{region}/"
        f"{filter_id}_{region}_{resolution}_{latest_ts}.json"
    )
    data = safe_get_json(data_url)
    series = data.get("series", [])
    if not series:
        raise RuntimeError("SMARD: keine Zeitreihe für Strompreis gefunden.")

    df = pd.DataFrame(series, columns=["timestamp", "strompreis_eur_mwh"])
    df["time"] = to_berlin_time(df["timestamp"], unit="ms")
    df["strompreis_eur_mwh"] = as_numeric(df["strompreis_eur_mwh"])
    df = df.drop(columns=["timestamp"]).sort_values("time").dropna(subset=["strompreis_eur_mwh"])
    return df

# =========================================================
# Energy-Charts – generischer Parser
# =========================================================
def parse_energy_charts_payload(data):
    """
    Unterstützt mehrere API-Formate:
    1) dict mit unix_seconds + production_types[{name, data}]
    2) dict mit unix_seconds + Listen je Feld
    3) list[dict]
    """
    # Format 1
    if isinstance(data, dict):
        unix_seconds = data.get("unix_seconds")
        production_types = data.get("production_types")

        if isinstance(unix_seconds, list) and isinstance(production_types, list):
            df = pd.DataFrame({"time": to_berlin_time_s(unix_seconds)})
            for item in production_types:
                name = item.get("name", "Unbekannt")
                values = item.get("data", [])
                if isinstance(values, list) and len(values) == len(df):
                    df[name] = as_numeric(values)
            if len(df.columns) > 1:
                return df.sort_values("time")

        # Format 2
        if isinstance(unix_seconds, list):
            df = pd.DataFrame({"time": to_berlin_time_s(unix_seconds)})
            for key, values in data.items():
                if key == "unix_seconds":
                    continue
                if isinstance(values, list) and len(values) == len(df):
                    df[key] = as_numeric(values)
            if len(df.columns) > 1:
                return df.sort_values("time")

    # Format 3
    if isinstance(data, list) and data:
        df = pd.DataFrame(data)
        for candidate in ["time", "date", "datetime", "timestamp", "unix_seconds"]:
            if candidate in df.columns:
                if candidate == "unix_seconds":
                    df["time"] = to_berlin_time_s(df[candidate])
                else:
                    parsed = pd.to_datetime(df[candidate], errors="coerce", utc=True)
                    try:
                        parsed = parsed.dt.tz_convert("Europe/Berlin")
                    except Exception:
                        pass
                    df["time"] = parsed
                break

        if "time" in df.columns:
            for col in df.columns:
                if col != "time":
                    df[col] = as_numeric(df[col])
            return df.sort_values("time")

    raise RuntimeError(
        f"Energy-Charts-Datenformat nicht erkannt: "
        f"{list(data.keys())[:20] if isinstance(data, dict) else type(data)}"
    )

# =========================================================
# Energy-Charts – Stromproduktion
# =========================================================
@st.cache_data(ttl=3600)
def load_stromproduktion(days=7):
    end_date = datetime.utcnow().date()
    start_date = end_date - timedelta(days=days)

    # total_power und public_power als Fallback-Kette
    endpoints = [
        f"https://api.energy-charts.info/total_power?country=de&start={start_date.isoformat()}&end={end_date.isoformat()}",
        f"https://api.energy-charts.info/public_power?country=de&start={start_date.isoformat()}&end={end_date.isoformat()}",
    ]

    last_error = None
    for url in endpoints:
        try:
            data = safe_get_json(url)
            df = parse_energy_charts_payload(data)
            if not df.empty:
                return df
        except Exception as e:
            last_error = e

    raise RuntimeError(f"Energy-Charts Stromproduktion nicht ladbar: {last_error}")

# =========================================================
# Erneuerbaren-Anteil aus Stromproduktion ableiten
# =========================================================
def build_renewable_share(df_total):
    if df_total is None or df_total.empty:
        raise RuntimeError("Keine Stromproduktionsdaten vorhanden.")

    renewable_keywords = [
        "solar", "pv", "wind", "hydro", "water", "biomass", "renewable", "geothermal"
    ]

    value_cols = [c for c in df_total.columns if c != "time"]
    renewable_cols = [
        c for c in value_cols
        if any(k in c.lower() for k in renewable_keywords)
    ]

    if not renewable_cols:
        raise RuntimeError(f"Keine erneuerbaren Spalten erkannt. Vorhanden: {value_cols}")

    df = df_total.copy()
    df["gesamt"] = df[value_cols].sum(axis=1, skipna=True)
    df["erneuerbare_summe"] = df[renewable_cols].sum(axis=1, skipna=True)
    df["erneuerbaren_anteil_prozent"] = (df["erneuerbare_summe"] / df["gesamt"]) * 100
    return df, renewable_cols

# =========================================================
# Energy-Charts – Installierte Leistung (Ausbau)
# =========================================================
@st.cache_data(ttl=3600)
def load_installed_power():
    year = datetime.utcnow().year
    start_date = f"{year}-01-01"
    end_date = datetime.utcnow().date().isoformat()

    url = (
        "https://api.energy-charts.info/installed_power"
        f"?country=de&start={start_date}&end={end_date}"
    )
    data = safe_get_json(url)
    df = parse_energy_charts_payload(data)

    if df.empty or len(df.columns) <= 1:
        raise RuntimeError("Energy-Charts: keine installierten Leistungsdaten erkannt.")

    return df

def build_ausbau_series(df_installed):
    if df_installed is None or df_installed.empty:
        raise RuntimeError("Keine Ausbau-Daten vorhanden.")

    cols = [c for c in df_installed.columns if c != "time"]

    wind_cols = [
        c for c in cols
        if any(k in c.lower() for k in ["wind", "onshore", "offshore"])
    ]
    solar_cols = [
        c for c in cols
        if any(k in c.lower() for k in ["solar", "pv", "photovoltaic"])
    ]

    out = df_installed[["time"]].copy()

    if wind_cols:
        out["wind_gesamt_mw"] = df_installed[wind_cols].sum(axis=1, skipna=True)
    if solar_cols:
        out["solar_gesamt_mw"] = df_installed[solar_cols].sum(axis=1, skipna=True)

    if len(out.columns) == 1:
        raise RuntimeError(f"Keine Wind-/Solarspalten erkannt. Vorhanden: {cols}")

    return out, wind_cols, solar_cols

# =========================================================
# Tankerkönig – Spritpreis (optional)
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
        raise RuntimeError("Tankerkönig: keine E5-Preise gefunden.")

    return pd.DataFrame({
        "time": [pd.Timestamp.now(tz="Europe/Berlin")],
        "spritpreis_eur_l": [sum(prices) / len(prices)]
    })

# =========================================================
# AGSI – Gasspeicher (optional)
# =========================================================
@st.cache_data(ttl=3600)
def load_gasspeicher():
    if not AGSI_API_KEY:
        return None

    headers = {
        "x-key": AGSI_API_KEY,
        "User-Agent": "Mozilla/5.0",
    }

    data = safe_get_json("https://agsi.gie.eu/api?country=DE", headers=headers)
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

    for col in ["full", "gasInStorage", "workingGasVolume"]:
        if col in df.columns:
            df[col] = as_numeric(df[col])

    return df.sort_values("time")

# =========================================================
# Daten laden
# =========================================================
errors = {}

try:
    zeit_snapshot = load_zeit_snapshot()
except Exception as e:
    zeit_snapshot = None
    errors["zeit"] = str(e)

try:
    df_strompreis = load_smard_strompreis().tail(24 * 30)
except Exception as e:
    df_strompreis = None
    errors["strompreis"] = str(e)

try:
    df_total = load_stromproduktion(days=7)
except Exception as e:
    df_total = None
    errors["stromproduktion"] = str(e)

try:
    df_ren, ren_cols = build_renewable_share(df_total)
except Exception as e:
    df_ren = None
    ren_cols = []
    errors["erneuerbare"] = str(e)

try:
    df_installed = load_installed_power()
    df_ausbau, wind_cols, solar_cols = build_ausbau_series(df_installed)
except Exception as e:
    df_installed = None
    df_ausbau = None
    wind_cols = []
    solar_cols = []
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
# Aktuelle Kennzahlen
# =========================================================
k1, k2, k3, k4 = st.columns(4)

with k1:
    if df_strompreis is not None and not df_strompreis.empty:
        val = df_strompreis["strompreis_eur_mwh"].dropna().iloc[-1]
        st.metric("Strompreis", f"{val:.2f} €/MWh")
        st.caption(f"SMARD, Stand: {df_timeframe_label(df_strompreis)}")
    elif zeit_snapshot and zeit_snapshot.get("strom_ct_kwh") is not None:
        st.metric("Strompreis", f"{zeit_snapshot['strom_ct_kwh']:.1f} ct/kWh")
        st.caption("ZEIT-Snapshot")
    else:
        st.metric("Strompreis", "–")

with k2:
    if df_ren is not None and not df_ren.empty:
        val = df_ren["erneuerbaren_anteil_prozent"].dropna().iloc[-1]
        st.metric("Erneuerbare", f"{val:.1f} %")
        st.caption(f"Energy-Charts, Stand: {df_timeframe_label(df_ren)}")
    else:
        st.metric("Erneuerbare", "–")

with k3:
    if df_sprit is not None and not df_sprit.empty:
        val = df_sprit["spritpreis_eur_l"].dropna().iloc[-1]
        st.metric("Spritpreis (E5)", f"{val:.3f} €/l")
        st.caption(f"Tankerkönig, Stand: {df_timeframe_label(df_sprit)}")
    elif zeit_snapshot and zeit_snapshot.get("sprit_eur_l") is not None:
        st.metric("Spritpreis (E5)", f"{zeit_snapshot['sprit_eur_l']:.3f} €/l")
        st.caption("ZEIT-Snapshot")
    else:
        st.metric("Spritpreis (E5)", "–")

with k4:
    if df_storage is not None and "full" in df_storage.columns and not df_storage["full"].dropna().empty:
        val = df_storage["full"].dropna().iloc[-1]
        date_str = pd.to_datetime(df_storage["time"]).max().strftime("%d.%m.%Y")
        st.metric("Gasspeicher", f"{val:.1f} %")
        st.caption(f"AGSI, Stand: {date_str}")
    else:
        st.metric("Gasspeicher", "–")

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

# ---------------------------------------------------------
# Windkraftausbau
# ---------------------------------------------------------
with tabs[0]:
    st.subheader("Windkraftausbau / installierte Leistung")
    if df_ausbau is not None and "wind_gesamt_mw" in df_ausbau.columns:
        latest = df_ausbau["wind_gesamt_mw"].dropna().iloc[-1]
        first = df_ausbau["wind_gesamt_mw"].dropna().iloc[0]
        delta = latest - first

        c1, c2 = st.columns(2)
        with c1:
            st.metric("Installierte Windleistung", f"{latest/1000:.2f} GW")
        with c2:
            st.metric("Veränderung seit Jahresanfang", f"{delta/1000:.2f} GW")

        fig = px.line(df_ausbau, x="time", y="wind_gesamt_mw", labels={"wind_gesamt_mw": "MW", "time": "Zeit"})
        st.plotly_chart(fig, use_container_width=True)
        st.caption(f"Erkannte Wind-Spalten: {wind_cols}")
        st.dataframe(df_ausbau[["time", "wind_gesamt_mw"]].tail(30), use_container_width=True)
    else:
        st.error(errors.get("ausbau", "Keine Wind-Ausbau-Daten verfügbar."))

# ---------------------------------------------------------
# Solarausbau
# ---------------------------------------------------------
with tabs[1]:
    st.subheader("Solarausbau / installierte Leistung")
    if df_ausbau is not None and "solar_gesamt_mw" in df_ausbau.columns:
        latest = df_ausbau["solar_gesamt_mw"].dropna().iloc[-1]
        first = df_ausbau["solar_gesamt_mw"].dropna().iloc[0]
        delta = latest - first

        c1, c2 = st.columns(2)
        with c1:
            st.metric("Installierte Solarleistung", f"{latest/1000:.2f} GW")
        with c2:
            st.metric("Veränderung seit Jahresanfang", f"{delta/1000:.2f} GW")

        fig = px.line(df_ausbau, x="time", y="solar_gesamt_mw", labels={"solar_gesamt_mw": "MW", "time": "Zeit"})
        st.plotly_chart(fig, use_container_width=True)
        st.caption(f"Erkannte Solar-Spalten: {solar_cols}")
        st.dataframe(df_ausbau[["time", "solar_gesamt_mw"]].tail(30), use_container_width=True)
    else:
        st.error(errors.get("ausbau", "Keine Solar-Ausbau-Daten verfügbar."))

# ---------------------------------------------------------
# Erneuerbare
# ---------------------------------------------------------
with tabs[2]:
    st.subheader("Anteil erneuerbarer Energien")
    if df_ren is not None:
        fig = px.line(
            df_ren.tail(24 * 7),
            x="time",
            y="erneuerbaren_anteil_prozent",
            labels={"erneuerbaren_anteil_prozent": "%", "time": "Zeit"}
        )
        st.plotly_chart(fig, use_container_width=True)
        st.caption(f"Aktualität: {df_timeframe_label(df_ren)} | erkannte EE-Spalten: {ren_cols}")
        st.dataframe(df_ren[["time", "erneuerbaren_anteil_prozent"]].tail(50), use_container_width=True)
    else:
        st.error(errors.get("erneuerbare", "Keine Daten verfügbar."))

# ---------------------------------------------------------
# Stromproduktion
# ---------------------------------------------------------
with tabs[3]:
    st.subheader("Stromproduktion / Strommix")
    if df_total is not None:
        cols = [c for c in df_total.columns if c != "time"]
        keep = []
        wanted = ["solar", "wind", "onshore", "offshore", "hydro", "biomass", "gas", "coal", "lignite"]
        for c in cols:
            if any(w in c.lower() for w in wanted):
                keep.append(c)
        keep = keep[:8]

        if keep:
            df_long = df_total[["time"] + keep].tail(24 * 3).melt(
                id_vars="time", var_name="Serie", value_name="Wert"
            )
            fig = px.area(df_long, x="time", y="Wert", color="Serie")
            st.plotly_chart(fig, use_container_width=True)
            st.caption(f"Aktualität: {df_timeframe_label(df_total)}")
            st.dataframe(df_total.tail(50), use_container_width=True)
        else:
            st.warning(f"Keine passenden Produktionsspalten erkannt. Vorhanden: {cols}")
    else:
        st.error(errors.get("stromproduktion", "Keine Daten verfügbar."))

# ---------------------------------------------------------
# Strompreis
# ---------------------------------------------------------
with tabs[4]:
    st.subheader("Gewichteter Stromgroßhandelspreis (Day-Ahead)")
    if df_strompreis is not None:
        fig = px.line(
            df_strompreis,
            x="time",
            y="strompreis_eur_mwh",
            labels={"strompreis_eur_mwh": "€/MWh", "time": "Zeit"}
        )
        st.plotly_chart(fig, use_container_width=True)
        st.caption(f"Aktualität: {df_timeframe_label(df_strompreis)}")
        st.dataframe(df_strompreis.tail(100), use_container_width=True)
    else:
        st.error(errors.get("strompreis", "Keine Daten verfügbar."))

# ---------------------------------------------------------
# Spritpreis
# ---------------------------------------------------------
with tabs[5]:
    st.subheader("Spritpreis (Super E5)")
    if df_sprit is not None:
        st.metric("Aktueller Mittelwert", f"{df_sprit['spritpreis_eur_l'].iloc[-1]:.3f} €/l")
        st.caption("Mittelwert aus mehreren Großstadt-Abfragen über Tankerkönig.")
        st.dataframe(df_sprit, use_container_width=True)
    elif zeit_snapshot and zeit_snapshot.get("sprit_eur_l") is not None:
        st.metric("Aktueller Wert", f"{zeit_snapshot['sprit_eur_l']:.3f} €/l")
        st.caption("Fallback: aktueller ZEIT-Snapshot")
    else:
        if not TANKERKOENIG_API_KEY:
            st.info("Für Live-Spritpreise: TANKERKOENIG_API_KEY in den Streamlit-Secrets hinterlegen.")
        else:
            st.error(errors.get("spritpreis", "Keine Daten verfügbar."))

# ---------------------------------------------------------
# Gaspreis
# ---------------------------------------------------------
with tabs[6]:
    st.subheader("Gaspreis")
    if zeit_snapshot and zeit_snapshot.get("gas_ct_kwh") is not None:
        st.metric("Aktueller Gaspreis", f"{zeit_snapshot['gas_ct_kwh']:.1f} ct/kWh")
        st.caption("Quelle: aktueller ZEIT-Energiemonitor-Snapshot")
        st.write("In dieser Version ist der Gaspreis bewusst als aktueller Snapshot eingebunden, nicht als vollständige Zeitreihe.")
    else:
        st.error("Gaspreis-Snapshot konnte aktuell nicht geladen werden.")

# ---------------------------------------------------------
# Gasspeicher
# ---------------------------------------------------------
with tabs[7]:
    st.subheader("Füllstand Gasspeicher")
    if df_storage is not None and "full" in df_storage.columns:
        fig = px.line(df_storage, x="time", y="full", labels={"full": "%", "time": "Zeit"})
        st.plotly_chart(fig, use_container_width=True)
        st.caption("AGSI veröffentlicht neue Daten nur einmal täglich.")
        st.dataframe(df_storage.tail(50), use_container_width=True)
    else:
        if not AGSI_API_KEY:
            st.info("Für den Gasspeicher: AGSI_API_KEY in den Streamlit-Secrets hinterlegen.")
        else:
            st.error(errors.get("gasspeicher", "Keine Daten verfügbar."))

# =========================================================
# Debug-Bereich
# =========================================================
with st.expander("Debug / Fehlermeldungen anzeigen"):
    if errors:
        st.json(errors)
    else:
        st.write("Keine Fehler erkannt.")
