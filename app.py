"""Test minimal — vérifie que Streamlit Cloud charge quelque chose."""
import streamlit as st

st.set_page_config(page_title="Hello Avalanch", layout="centered")
st.title("👋 Hello Avalanch")
st.write("Si tu vois ce texte, Streamlit Cloud marche correctement.")
st.write("On peut donc partir du principe que le bug est dans le code de l'app, pas dans la config.")
st.success("Tout est OK côté infrastructure.")
