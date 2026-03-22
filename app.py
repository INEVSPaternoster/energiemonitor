import streamlit as st
import pandas as pd
import requests
import plotly.express as px
from datetime import datetime, timedelta

st.set_page_config(layout="wide")
st.title("Energiemonitor – Strompreis, Strommix und Erneuerbaren-Anteil")

# =========================
# Konfiguration
# =========================
SMARD_FILTER_ID = 4169   # gewichteter Day-Ahead-Großhandelspreis
SMARD_REGION = "DE"
SMARD_RESOLUTION = "hour"

EC_COUNTRY = "de"

# =========================
# Hilfsfunktionen
# =========================
def _find_first_list_of_len(obj, min_len=2):
    """Sucht rekursiv die erste Liste mit mindestens min_len Einträgen."""
    if isinstance(obj, list):
        if len(obj) >= min_len:
            return obj
        for item in obj:
            result = _find_first_list_of_len(item, min_len=min_len)
            if result is not None:
                return result
    elif isinstance(obj, dict):
        for value in obj.values():
            result = _find_first_list_of_len(value, min_len=min_len)
            if result is not None:
                return result
    return None


def _find_all_numeric_lists(obj, path="root", results=None):
    """Findet rekursiv numerische Listen in verschachtelten JSONs."""
    if results is None:
        results = []
    if isinstance(obj, list):
        if obj and all(isinstance(x, (int, float)) or x is None for x in obj):
            results.append((path, obj))
        else:
            for i, item in enumerate(obj):
                _find_all_numeric_lists(item, f"{path}[{i}]", results)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _find_all_numeric_lists(v, f"{path}.{k}", results)
    return results


def _pick_timestamp_list(obj):
    """Sucht eine plausible Zeitstempel-Liste."""
    candidates = _find_all_numeric_lists(obj)
    for path, values in candidates:
        if len(values) < 10:
            continue
        numeric = [v for v in values if v is not None]
        if not numeric:
            continue
        # unix Sekunden
        if all(1_000_000_000 <= v <= 2_500_000_000 for v in numeric[:20]):
            return path, values, "s"
        # unix Millisekunden
        if all(1_000_000_000_000 <= v <= 2_500_000_000_000 for v in numeric[:20]):
            return path, values, "ms"
    return None, None, None


def _pick_value_series(obj, target_len):
    """Sucht numerische Serien mit gleicher Länge wie die Zeitstempel."""
    candidates = _find_all_numeric_lists(obj)
    series = []
    for path, values in candidates:
        if len(values) != target_len:
            continue
        numeric = [v for v in values if isinstance(v, (int, float))]
        if len(numeric) < max(5, target_len // 3):
            continue
        series.append((path, values))
    return series


def _series_name_from_path(path):
    name = path.split(".")[-1]
    name = name.replace("[", "_").replace("]", "")
    return name


# =========================
# SMARD – Strompreis
# =========================
@st.cache_data(ttl=3600)
def get_smard_price():
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    index_url = (
        f"https://www.smard.de/app/chart_data/"
        f"{SMARD_FILTER_ID}/{SMARD_REGION}/index_{SMARD_RESOLUTION}.json"
    )
    r = session.get(index_url, timeout=30)
    r.raise_for_status()
    index_data = r.json()

    timestamps = index_data.get("timestamps", [])
    if not timestamps:
        raise RuntimeError("SMARD: keine Timestamps gefunden.")

    latest_ts = timestamps[-1]
    data_url = (
        f"https://www.smard.de/app/chart_data/"
        f"{SMARD_FILTER_ID}/{SMARD_REGION}/"
        f"{SMARD_FILTER_ID}_{SMARD_REGION}_{SMARD_RESOLUTION}_{latest_ts}.json"
    )
    r = session.get(data_url, timeout=30)
    r.raise_for_status()
    data = r.json()

    series = data.get("series", [])
    if not series:
        raise RuntimeError("SMARD: keine Zeitreihendaten gefunden.")

    df = pd.DataFrame(series, columns=["timestamp", "price_eur_per_mwh"])
    df["time"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.tz_convert("Europe/Berlin")
    df = df.drop(columns=["timestamp"]).sort_values("time")
    return df


# =========================
# Energy Charts – generisch
# =========================
@st.cache_data(ttl=3600)
def get_energy_charts_json(endpoint, start_date, end_date, country=EC_COUNTRY):
    url = (
        f"https://api.energy-charts.info/{endpoint}"
        f"?country={country}&start={start_date}&end={end_date}"
    )
    r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return r.json()


def parse_energy_charts_timeseries(data, preferred_keywords=None, max_series=8):
    """
    Versucht, aus dem Energy-Charts-JSON automatisch einen DataFrame
    mit Zeitreihe + mehreren Werten zu bauen.
    """
    preferred_keywords = preferred_keywords or []

    ts_path, ts_values, ts_unit = _pick_timestamp_list(data)
    if ts_values is None:
        raise RuntimeError("Energy-Charts: keine Zeitstempel im JSON gefunden.")

    dt = pd.to_datetime(ts_values, unit=ts_unit, utc=True).tz_convert("Europe/Berlin")
    target_len = len(ts_values)

    value_series = _pick_value_series(data, target_len)
    if not value_series:
        raise RuntimeError("Energy-Charts: keine Datenserien gefunden.")

    # Zeitstempelpfad herausfiltern
    value_series = [(p, v) for p, v in value_series if p != ts_path]

    # bevorzugte Serien nach Keywords zuerst
    def rank_item(item):
        path, _ = item
        p = path.lower()
        score = 100
        for i, kw in enumerate(preferred_keywords):
            if kw.lower() in p:
                score = i
                break
        return score, len(path)

    value_series = sorted(value_series, key=rank_item)

    df = pd.DataFrame({"time": dt})
    added = 0
    used_names = set()

    for path, values in value_series:
        name = _series_name_from_path(path)

        # lesbarere Namen
        for noisy in ["production_types.", "data.", "series.", "root."]:
            name = name.replace(noisy, "")
        name = name.replace("_data", "").replace("_values", "")

        if name in used_names:
            continue

        # nur sinnvolle Serien mit echter Varianz
        s = pd.Series(values)
        if s.nunique(dropna=True) <= 1:
            continue

        df[name] = s
        used_names.add(name)
        added += 1
        if added >= max_series:
            break

    if len(df.columns) <= 1:
        raise RuntimeError("Energy-Charts: keine brauchbaren Datenserien extrahiert.")

    return df


@st.cache_data(ttl=3600)
def get_public_power_df(days=3):
    end_date = datetime.utcnow().date()
    start_date = end_date - timedelta(days=days)

    data = get_energy_charts_json(
        endpoint="public_power",
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat()
    )

    # Typische Serien im Strommix zuerst priorisieren
    keywords = [
        "solar", "pv", "wind", "onshore", "offshore",
        "biomass", "hydro", "coal", "lignite", "gas"
    ]
    return parse_energy_charts_timeseries(data, preferred_keywords=keywords, max_series=10)


@st.cache_data(ttl=3600)
def get_renewable_share_df(days=30):
    end_date = datetime.utcnow().date()
    start_date = end_date - timedelta(days=days)

    # erst feinere Zeitreihe versuchen
    errors = []
    for endpoint in ["ren_share", "ren_share_daily_avg"]:
        try:
            data = get_energy_charts_json(
                endpoint=endpoint,
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat()
            )
            keywords = ["renew", "share", "load", "generation"]
            df = parse_energy_charts_timeseries(data, preferred_keywords=keywords, max_series=6)
            return endpoint, df
        except Exception as e:
            errors.append(f"{endpoint}: {e}")

    raise RuntimeError(" / ".join(errors))


# =========================
# Daten laden
# =========================
price_error = None
mix_error = None
share_error = None

try:
    df_price = get_smard_price().tail(24 * 30)
except Exception as e:
    price_error = str(e)
    df_price = None

try:
    df_mix = get_public_power_df(days=3)
except Exception as e:
    mix_error = str(e)
    df_mix = None

try:
    share_endpoint, df_share = get_renewable_share_df(days=30)
except Exception as e:
    share_error = str(e)
    share_endpoint, df_share = None, None

# =========================
# Layout
# =========================
col1, col2, col3 = st.columns(3)

with col1:
    st.subheader("Strompreis")
    if df_price is not None:
        latest_price = df_price["price_eur_per_mwh"].dropna().iloc[-1]
        st.metric("Letzter Wert", f"{latest_price:.2f} €/MWh")
    else:
        st.error(f"Preis konnte nicht geladen werden: {price_error}")

with col2:
    st.subheader("Strommix")
    if df_mix is not None:
        st.metric("Serien erkannt", str(len(df_mix.columns) - 1))
    else:
        st.error(f"Strommix konnte nicht geladen werden: {mix_error}")

with col3:
    st.subheader("Erneuerbare")
    if df_share is not None:
        st.metric("Quelle", share_endpoint)
    else:
        st.error(f"Erneuerbaren-Anteil konnte nicht geladen werden: {share_error}")

tab1, tab2, tab3, tab4 = st.tabs([
    "Strompreis",
    "Strommix",
    "Erneuerbaren-Anteil",
    "Rohdaten"
])

with tab1:
    st.markdown("### Gewichteter Stromgroßhandelspreis (Day-Ahead)")
    if df_price is not None:
        fig = px.line(
            df_price,
            x="time",
            y="price_eur_per_mwh",
            labels={"time": "Zeit", "price_eur_per_mwh": "€/MWh"},
            title="Deutschland – letzte 30 Tage"
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.warning("Für den Strompreis konnten aktuell keine Daten geladen werden.")

with tab2:
    st.markdown("### Strommix / öffentliche Nettostromerzeugung")
    if df_mix is not None:
        value_cols = [c for c in df_mix.columns if c != "time"]

        # nur die ersten 6 nicht-leeren Serien zeigen
        usable = []
        for c in value_cols:
            if df_mix[c].notna().sum() > 5:
                usable.append(c)
        usable = usable[:6]

        if usable:
            df_long = df_mix[["time"] + usable].melt(
                id_vars="time",
                var_name="Serie",
                value_name="Wert"
            )
            fig = px.area(
                df_long,
                x="time",
                y="Wert",
                color="Serie",
                title="Erkannte Strommix-Serien – letzte Tage"
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Es wurden Daten geladen, aber keine gut darstellbaren Serien erkannt.")

        st.caption("Hinweis: Die Seriennamen werden automatisch aus dem API-JSON abgeleitet.")
    else:
        st.warning("Für den Strommix konnten aktuell keine Daten geladen werden.")

with tab3:
    st.markdown("### Anteil erneuerbarer Energien")
    if df_share is not None:
        value_cols = [c for c in df_share.columns if c != "time"]

        usable = []
        for c in value_cols:
            if df_share[c].notna().sum() > 5:
                usable.append(c)
        usable = usable[:4]

        if usable:
            df_long = df_share[["time"] + usable].melt(
                id_vars="time",
                var_name="Serie",
                value_name="Wert"
            )
            fig = px.line(
                df_long,
                x="time",
                y="Wert",
                color="Serie",
                title=f"Erneuerbaren-Anteil – Quelle: {share_endpoint}"
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Es wurden Daten geladen, aber keine darstellbaren Serien erkannt.")
    else:
        st.warning("Für den Erneuerbaren-Anteil konnten aktuell keine Daten geladen werden.")

with tab4:
    st.markdown("### Rohdaten")
    if df_price is not None:
        st.markdown("**SMARD – Strompreis**")
        st.dataframe(df_price.tail(100), use_container_width=True)
    if df_mix is not None:
        st.markdown("**Energy-Charts – Strommix**")
        st.dataframe(df_mix.tail(50), use_container_width=True)
    if df_share is not None:
        st.markdown("**Energy-Charts – Erneuerbaren-Anteil**")
        st.dataframe(df_share.tail(50), use_container_width=True)
