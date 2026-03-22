def parse_energy_charts_payload(data):
    """
    Unterstützt mehrere Energy-Charts-Formate:
    1) dict mit unix_seconds + production_types[{name, data}]
    2) dict mit time + production_types[{name, data}]
    3) dict mit unix_seconds + flachen Listen
    4) list[dict]
    """

    if isinstance(data, dict):
        production_types = data.get("production_types")

        # Format 1: unix_seconds + production_types
        unix_seconds = data.get("unix_seconds")
        if isinstance(unix_seconds, list) and isinstance(production_types, list):
            df = pd.DataFrame({
                "time": pd.to_datetime(unix_seconds, unit="s", utc=True).tz_convert("Europe/Berlin")
            })
            for item in production_types:
                name = item.get("name", "Unbekannt")
                values = item.get("data", [])
                if isinstance(values, list) and len(values) == len(df):
                    df[name] = pd.to_numeric(values, errors="coerce")
            if len(df.columns) > 1:
                return df.sort_values("time")

        # Format 2: time + production_types
        time_values = data.get("time")
        if isinstance(time_values, list) and isinstance(production_types, list):
            parsed_time = pd.to_datetime(time_values, errors="coerce", utc=True)
            try:
                parsed_time = parsed_time.tz_convert("Europe/Berlin")
            except Exception:
                pass

            df = pd.DataFrame({"time": parsed_time})

            for item in production_types:
                name = item.get("name", "Unbekannt")
                values = item.get("data", [])
                if isinstance(values, list) and len(values) == len(df):
                    df[name] = pd.to_numeric(values, errors="coerce")

            if len(df.columns) > 1:
                return df.sort_values("time")

        # Format 3: time + flache Listen
        if isinstance(time_values, list):
            parsed_time = pd.to_datetime(time_values, errors="coerce", utc=True)
            try:
                parsed_time = parsed_time.tz_convert("Europe/Berlin")
            except Exception:
                pass

            df = pd.DataFrame({"time": parsed_time})

            for key, values in data.items():
                if key in ["time", "last_update", "deprecated"]:
                    continue
                if isinstance(values, list) and len(values) == len(df):
                    df[key] = pd.to_numeric(values, errors="coerce")

            if len(df.columns) > 1:
                return df.sort_values("time")

        # Format 4: unix_seconds + flache Listen
        if isinstance(unix_seconds, list):
            df = pd.DataFrame({
                "time": pd.to_datetime(unix_seconds, unit="s", utc=True).tz_convert("Europe/Berlin")
            })
            for key, values in data.items():
                if key in ["unix_seconds", "last_update", "deprecated"]:
                    continue
                if isinstance(values, list) and len(values) == len(df):
                    df[key] = pd.to_numeric(values, errors="coerce")

            if len(df.columns) > 1:
                return df.sort_values("time")

    # Format 5: Liste von Datensätzen
    if isinstance(data, list) and data:
        df = pd.DataFrame(data)

        for candidate in ["time", "date", "datetime", "timestamp", "unix_seconds"]:
            if candidate in df.columns:
                if candidate == "unix_seconds":
                    df["time"] = pd.to_datetime(df[candidate], unit="s", utc=True).dt.tz_convert("Europe/Berlin")
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
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            return df.sort_values("time")

    raise RuntimeError(
        f"Energy-Charts-Datenformat nicht erkannt: "
        f"{list(data.keys())[:20] if isinstance(data, dict) else type(data)}"
    )
