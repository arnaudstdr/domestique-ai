"""
Dashboard Streamlit DomestiqueAI.

Affiche les courbes CTL/ATL/TSB et les détails d'activités, avec un bouton
de synchronisation Strava et des filtres par plage de dates.

Lancement : `streamlit run domestique_ai/app/dashboard.py`
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from domestique_ai.config import get_db_path, get_strava_credentials
from domestique_ai.ingestion.strava import StravaAuthError, StravaClient, sync_activities
from domestique_ai.processing.analyzer import calculate_ctl_atl_tsb, fetch_activities_from_db

st.set_page_config(page_title="DomestiqueAI – Dashboard", layout="wide")
st.title("🚴‍♂️ DomestiqueAI – Tableau de bord d'entraînement")


def _tsb_zone_label(tsb: float) -> tuple[str, str]:
    """Retourne (libellé, emoji) pour la zone de forme actuelle."""
    if tsb > 5:
        return "Frais", "🟢"
    if tsb >= -10:
        return "Optimal", "🟡"
    if tsb >= -20:
        return "Fatigué", "🟠"
    return "Surentraîné", "🔴"


with st.sidebar:
    st.header("Synchronisation")
    if st.button("🔄 Synchroniser Strava", width="stretch"):
        client_id, client_secret, _ = get_strava_credentials()
        if not (client_id and client_secret):
            st.error("STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET absents du .env.")
        else:
            try:
                with st.spinner("Récupération des activités…"):
                    client = StravaClient.from_tokens_file(client_id, client_secret)
                    inserted = sync_activities(client)
                st.success(f"{inserted} nouvelle(s) activité(s) ajoutée(s).")
            except StravaAuthError as exc:
                st.error(str(exc))
            except Exception as exc:  # noqa: BLE001
                st.error(f"Erreur de synchronisation : {exc}")

activities = fetch_activities_from_db()

if not activities:
    st.warning(
        f"Aucune activité dans la base ({get_db_path()}). "
        "Lancez d'abord `python -m domestique_ai.ingestion.strava_oauth_flow` "
        "ou cliquez sur **Synchroniser Strava** dans la barre latérale."
    )
else:
    df = pd.DataFrame(activities)
    df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None)

    curves = pd.DataFrame(calculate_ctl_atl_tsb(activities))
    curves["date"] = pd.to_datetime(curves["date"])

    min_date = df["date"].min().date()
    max_date = df["date"].max().date()
    default_start = max(min_date, max_date - dt.timedelta(days=180))

    date_range = st.sidebar.date_input(
        "Plage de dates",
        value=(default_start, max_date),
        min_value=min_date,
        max_value=max_date,
    )
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range
    else:
        start_date, end_date = default_start, max_date

    start_ts = pd.Timestamp(start_date)
    end_ts = pd.Timestamp(end_date) + pd.Timedelta(days=1)
    df_filtered = df[(df["date"] >= start_ts) & (df["date"] < end_ts)]
    curves_filtered = curves[(curves["date"] >= start_ts) & (curves["date"] < end_ts)]

    if not curves_filtered.empty:
        last = curves_filtered.iloc[-1]
        label, emoji = _tsb_zone_label(last["TSB"])
        cols = st.columns(4)
        cols[0].metric("CTL (forme)", f"{last['CTL']:.1f}")
        cols[1].metric("ATL (fatigue)", f"{last['ATL']:.1f}")
        cols[2].metric("TSB (fraîcheur)", f"{last['TSB']:.1f}")
        cols[3].metric("Zone", f"{emoji} {label}")

    st.subheader("Évolution de la charge d'entraînement")
    if curves_filtered.empty:
        st.info("Aucune donnée sur la plage sélectionnée.")
    else:
        st.line_chart(curves_filtered.set_index("date")[["CTL", "ATL", "TSB"]])

    st.subheader("Détail des activités")
    st.dataframe(
        df_filtered.sort_values("date", ascending=False),
        width="stretch",
    )
