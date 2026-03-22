import streamlit as st
import pandas as pd
import requests
import plotly.express as px

st.set_page_config(layout="wide")
st.title("Energiemonitor")

def get_smard():
    url = "https://www.smard.de/app/chart_data/1001226/DE/1001226_DE_quarterhour.json"
    data = requests.get(url).json()

    df = pd.DataFrame(data["series"], columns=["timestamp", "price"])
    df["time"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.tail(200)

    return df

df = get_smard()

fig = px.line(df, x="time", y="price", title="Strompreis (Day-Ahead, DE)")
st.plotly_chart(fig, use_container_width=True)
