"""
App Streamlit pour visualiser la charge d'entraînement (CTL, ATL, TSB) et explorer les activités Strava.
"""
import streamlit as st
import pandas as pd
import os
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from processing.analyzer import fetch_activities_from_db, calculate_ctl_atl_tsb

st.set_page_config(page_title="DomestiqueAI – Dashboard", layout="wide")
st.title("🚴‍♂️ DomestiqueAI – Tableau de bord d'entraînement")

# Chargement des activités
data = fetch_activities_from_db()
df = pd.DataFrame(data)

if df.empty:
    st.warning("Aucune activité trouvée dans la base. Importez d'abord vos données Strava.")
    st.stop()

# Calcul des courbes de charge
courbes = calculate_ctl_atl_tsb(data)
df_courbes = pd.DataFrame(courbes)

# Affichage des courbes CTL/ATL/TSB
st.subheader("Évolution de la charge d'entraînement")
st.line_chart(df_courbes.set_index('date')[['CTL', 'ATL', 'TSB']])

# Exploration des activités
st.subheader("Détail des activités")
st.dataframe(df.sort_values('date', ascending=False), use_container_width=True)
