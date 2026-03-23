import re
from datetime import datetime, timedelta

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

st.set_page_config(layout="wide", page_title="Energiemonitor Deutschland")
st.title("Energiemonitor Deutschland")

HEADERS = {"User-Agent": "Mozilla/5.0 (Energiemonitor)"}

AGSI_API_KEY = st.secrets.get("AGSI_API_KEY", "")
TANKERKOENIG_API_KEY = st.secrets.get("TANKERKOENIG_API_KEY", "")

ZEIT_URL = "https://www.zeit.de/wirtschaft/energiemonitor-strompreis-gaspreis-erneuerbare-energien-ausbau"

# =========================================================
# Helpers
# =========================================================
def safe_get(url, headers=None, timeout=20):
    r = requests.get(url, headers=headers or HEADERS, timeout=timeout)
    r.raise_for_status()
    return r

def safe_get_json(url, headers=None, timeout=20):
    return safe_get(url, headers=headers, timeout=timeout).json()

def as_numeric(series):
    return pd.to_numeric(series, errors="coerce")

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

# =========================================================
# SMARD – Strompreis
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
# ZEIT – Snapshot für Gas / Sprit Fallback
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
# Energy-Charts Parser
# =========================================================
def parse_energy_charts_payload(data):
    if isinstance(data, dict):
        production_types = data.get("production_types")
        unix_seconds = data.get("unix_seconds")
        time_values = data.get("time")

        # 1) unix_seconds + production_types
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

        # 2) time + production_types
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

        # 3) time + flache Listen
        if isinstance(time_values, list):
            df = pd.DataFrame({"time": parse_time_any(time_values)})
            for key, values in data.items():
                if key in ["time", "last_update", "deprecated", "production_types"]:
                    continue
                if isinstance(values, list) and len(values) == len(df):
                    df[key] = pd.to_numeric(values, errors="coerce")
            if len(df.columns) > 1:
                return df.sort_values("time")

        # 4) unix_seconds + flache Listen
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

# =========================================================
# Energy-Charts Loader
# =========================================================
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
# Tankerkönig / AGSI optional
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
# Daten laden
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
# KPI
# =========================================================
c1, c2, c3, c4 = st.columns(4)

with c1:
    if df_strompreis is not None and not df_strompreis.empty:
        st.metric("Strompreis", f"{df_strompreis['strompreis_eur_mwh'].iloc[-1]:.2f} €/MWh")
        st.caption(f"SMARD, Stand: {latest_label(df_strompreis)}")
    else:
        st.metric("Strompreis", "–")

with c2:
    if df_ren is not None and not df_ren.empty:
        st.metric("Erneuerbare", f"{df_ren['erneuerbaren_anteil_prozent'].dropna().iloc[-1]:.1f} %")
        st.caption(f"Energy-Charts, Stand: {latest_label(df_ren)}")
    else:
        st.metric("Erneuerbare", "–")

with c3:
    if df_sprit is not None:
        st.metric("Spritpreis (E5)", f"{df_sprit['spritpreis_eur_l'].iloc[-1]:.3f} €/l")
        st.caption(f"Tankerkönig, Stand: {latest_label(df_sprit)}")
    elif zeit_snapshot and zeit_snapshot.get("sprit_eur_l") is not None:
        st.metric("Spritpreis (E5)", f"{zeit_snapshot['sprit_eur_l']:.3f} €/l")
        st.caption("ZEIT-Snapshot")
    else:
        st.metric("Spritpreis (E5)", "–")

with c4:
    if zeit_snapshot and zeit_snapshot.get("gas_ct_kwh") is not None:
        st.metric("Gaspreis", f"{zeit_snapshot['gas_ct_kwh']:.1f} ct/kWh")
        st.caption("ZEIT-Snapshot")
    else:
        st.metric("Gaspreis", "–")

# =========================================================
# Tabs
# =========================================================
tabs = st.tabs([
    "Strompreis",
    "Stromproduktion",
    "Erneuerbare",
    "Windkraftausbau",
    "Solarausbau",
    "Spritpreis",
    "Gaspreis",
    "Gasspeicher",
    "Debug",
])

with tabs[0]:
    st.subheader("Strompreis")
    if df_strompreis is not None:
        fig = px.line(df_strompreis.tail(24 * 7), x="time", y="strompreis_eur_mwh")
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(df_strompreis.tail(50), use_container_width=True)
    else:
        st.error(errors.get("strompreis", "Keine Daten"))

with tabs[1]:
    st.subheader("Stromproduktion")
    if df_total is not None:
        plot_cols = [c for c in df_total.columns if c != "time"][:6]
        st.write("Erkannte Spalten:", list(df_total.columns))
        if plot_cols:
            df_long = df_total[["time"] + plot_cols].tail(24 * 3).melt(
                id_vars="time", var_name="Serie", value_name="Wert"
            )
            fig = px.area(df_long, x="time", y="Wert", color="Serie")
            st.plotly_chart(fig, use_container_width=True)
        st.dataframe(df_total.tail(20), use_container_width=True)
    else:
        st.error(errors.get("stromproduktion", "Keine Daten"))

with tabs[2]:
    st.subheader("Erneuerbaren-Anteil")
    if df_ren is not None:
        fig = px.line(df_ren.tail(24 * 7), x="time", y="erneuerbaren_anteil_prozent")
        st.plotly_chart(fig, use_container_width=True)
        st.write("Erkannte EE-Spalten:", ren_cols)
        st.dataframe(df_ren[["time", "erneuerbaren_anteil_prozent"]].tail(20), use_container_width=True)
    else:
        st.error(errors.get("erneuerbare", "Keine Daten"))

with tabs[3]:
    st.subheader("Windkraftausbau")
    if df_ausbau is not None and "wind_gesamt_mw" in df_ausbau.columns:
        fig = px.line(df_ausbau, x="time", y="wind_gesamt_mw")
        st.plotly_chart(fig, use_container_width=True)
        st.write("Erkannte Wind-Spalten:", wind_cols)
        st.dataframe(df_ausbau[["time", "wind_gesamt_mw"]].tail(20), use_container_width=True)
    else:
        st.warning("Keine Windspalten erkannt.")

with tabs[4]:
    st.subheader("Solarausbau")
    if df_ausbau is not None and "solar_gesamt_mw" in df_ausbau.columns:
        fig = px.line(df_ausbau, x="time", y="solar_gesamt_mw")
        st.plotly_chart(fig, use_container_width=True)
        st.write("Erkannte Solar-Spalten:", solar_cols)
        st.dataframe(df_ausbau[["time", "solar_gesamt_mw"]].tail(20), use_container_width=True)
    else:
        st.warning("Keine Solarspalten erkannt.")

with tabs[5]:
    st.subheader("Spritpreis")
    if df_sprit is not None:
        st.dataframe(df_sprit, use_container_width=True)
    elif zeit_snapshot and zeit_snapshot.get("sprit_eur_l") is not None:
        st.metric("Fallback", f"{zeit_snapshot['sprit_eur_l']:.3f} €/l")
        st.caption("ZEIT-Snapshot")
    else:
        st.warning("Kein Spritpreis verfügbar.")

with tabs[6]:
    st.subheader("Gaspreis")
    if zeit_snapshot and zeit_snapshot.get("gas_ct_kwh") is not None:
        st.metric("Aktueller Gaspreis", f"{zeit_snapshot['gas_ct_kwh']:.1f} ct/kWh")
        st.caption("ZEIT-Snapshot")
    else:
        st.warning("Kein Gaspreis verfügbar.")

with tabs[7]:
    st.subheader("Gasspeicher")
    if df_storage is not None and "full" in df_storage.columns:
        fig = px.line(df_storage, x="time", y="full")
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(df_storage.tail(20), use_container_width=True)
    else:
        st.warning("Kein Gasspeicher verfügbar oder API-Key fehlt.")

with tabs[8]:
    st.subheader("Debug")
    st.json(errors if errors else {"status": "keine Fehler"})
    if strom_raw is not None:
        st.write("Top-Level-Keys Stromproduktion:", list(strom_raw.keys()) if isinstance(strom_raw, dict) else type(strom_raw))
    if raw_installed is not None:
        st.write("Top-Level-Keys Installed Power:", list(raw_installed.keys()) if isinstance(raw_installed, dict) else type(raw_installed))
