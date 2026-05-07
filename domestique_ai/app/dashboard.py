"""
Dashboard Streamlit DomestiqueAI.

Deux onglets :
- « Tableau de bord » : courbes CTL/ATL/TSB et détail des activités.
- « Coach » : chatbot qui analyse l'entraînement et propose des séances
  (Ollama + tool calling, voir domestique_ai.llm).

Lancement : `streamlit run domestique_ai/app/dashboard.py`
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import streamlit as st

from domestique_ai.config import (
    get_db_path,
    get_hr_max,
    get_hr_rest,
    get_ollama_model,
    get_strava_credentials,
)
from domestique_ai.export.fit import plan_to_zip
from domestique_ai.export.garmin_connect import GarminPushError
from domestique_ai.export.garmin_connect import credentials_present as garmin_credentials_present
from domestique_ai.export.garmin_connect import get_client as get_garmin_client
from domestique_ai.export.garmin_connect import push_plan as garmin_push_plan
from domestique_ai.export.garmin_connect import token_cache_present as garmin_token_cache_present
from domestique_ai.ingestion.strava import (
    StravaAuthError,
    StravaClient,
    backfill_activity_fields,
    backfill_hr_zones,
    backfill_sport_types,
    sync_activities,
)
from domestique_ai.llm.availability import (
    Availability,
    AvailabilityError,
    load_availability,
)
from domestique_ai.llm.coach import run_turn
from domestique_ai.llm.conversations import (
    append_message,
    delete_session,
    list_sessions,
    load_session,
    new_session_id,
)
from domestique_ai.llm.objectives import load_objective
from domestique_ai.llm.ollama_client import OllamaError
from domestique_ai.llm.plan_storage import (
    delete_plan,
    list_plans,
    load_plan,
    save_plan,
)
from domestique_ai.processing.analyzer import (
    HR_ZONE_KEYS,
    calculate_ctl_atl_tsb,
    fetch_activities_from_db,
    fetch_weight_history,
    recalculate_training_loads,
)
from domestique_ai.processing.gpx import build_gpx
from domestique_ai.processing.morning_metrics import (
    compute_baselines,
    detect_morning_alerts,
    fetch_morning_entry,
    fetch_morning_history,
    save_morning_entry,
)
from domestique_ai.processing.overtraining import detect_overtraining_signals
from domestique_ai.processing.plan_builder import build_training_plan

_ICON_PATH = Path(__file__).resolve().parents[2] / "icon.png"

_HR_ZONE_HRR_LABELS = ("< 60 %", "60–70 %", "70–80 %", "80–90 %", "≥ 90 %")
_HR_ZONE_HRR_BOUNDS = ((0.0, 0.60), (0.60, 0.70), (0.70, 0.80),
                       (0.80, 0.90), (0.90, 1.0))
_HR_ZONE_PCT_COL = "% du total"

st.set_page_config(
    page_title="DomestiqueAI",
    page_icon=str(_ICON_PATH) if _ICON_PATH.exists() else "🚴‍♂️",
    layout="wide",
)

_title_col, _icon_col = st.columns([6, 1])
with _title_col:
    st.title("🚴‍♂️ DomestiqueAI")
with _icon_col:
    if _ICON_PATH.exists():
        st.image(str(_ICON_PATH), width=110)


def _tsb_zone_label(tsb: float) -> tuple[str, str]:
    """Retourne (libellé, emoji) pour la zone de forme actuelle."""
    if tsb > 5:
        return "Frais", "🟢"
    if tsb >= -10:
        return "Optimal", "🟡"
    if tsb >= -20:
        return "Fatigué", "🟠"
    return "Surentraîné", "🔴"


def _format_hms(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def _is_ride(sport_type: str | None) -> bool:
    """Filtre vélo : tout sport_type Strava contenant "Ride".

    Couvre Ride, VirtualRide, MountainBikeRide, GravelRide, EBikeRide, etc.
    Exclut Walk, Run, Swim, Hike, Workout.
    """
    return bool(sport_type) and "Ride" in sport_type


def _format_km(meters: float) -> str:
    """Format français : `1 247,3 km`."""
    return f"{meters / 1000:,.1f} km".replace(",", " ").replace(".", ",")


def _format_hours(seconds: float) -> str:
    """Format compact : `12h 34`."""
    s = int(seconds)
    h, m = s // 3600, (s % 3600) // 60
    return f"{h}h {m:02d}"


def _hr_zone_bpm_ranges(hr_rest: float | None,
                        hr_max: float | None) -> list[str] | None:
    """Plages BPM par zone (Karvonen). None si HRrepos/HRmax non configurés."""
    if not (hr_rest and hr_max and hr_max > hr_rest):
        return None
    hrr = hr_max - hr_rest
    return [
        f"{round(hr_rest + lo * hrr)}–{round(hr_rest + hi * hrr)}"
        for lo, hi in _HR_ZONE_HRR_BOUNDS
    ]


_DETAIL_STREAM_KEYS = [
    "time", "latlng", "altitude", "heartrate",
    "cadence", "watts", "velocity_smooth", "distance",
]
_TIME_AXIS_LABEL = "temps (min)"
_ACTIVITY_ANALYSIS_PROMPT = (
    "Analyse l'activité Strava avec strava_id={strava_id}. "
    "Étape 1 : appelle `get_activity_details(strava_id={strava_id})` pour récupérer "
    "les chiffres (durée, distance, FC, charge, zones HR) ainsi que la date. "
    "Étape 2 : appelle `get_training_load_state` (CTL/ATL/TSB du jour) et "
    "`get_objective` pour le contexte. "
    "Étape 2 bis : appelle `get_planned_workout(date=<date ISO de l'activité>)` "
    "pour récupérer la séance qui était prévue ce jour-là dans le plan en cours. "
    "Compare le réalisé au prévu (kind, target_zone, durée, charge estimée) — "
    "ou note explicitement que la sortie était hors plan si aucun plan ne couvre "
    "cette date. "
    "Étape 3 : conclus en 4-6 lignes en répondant explicitement à : "
    "(1) ce que cette sortie a apporté physiologiquement (filière dominante, "
    "stimulus principal) ; "
    "(2) si elle a été *productive* ou *contre-productive* vu le TSB courant, "
    "l'objectif et la séance prévue (conforme au plan ? écart en intensité ou "
    "volume ? si écart, avec quel impact ?) ; "
    "(3) la séance ou la récup à privilégier ensuite. "
    "Sois concis, factuel, en français."
)


@st.cache_data(ttl=3600, show_spinner="Chargement de l'activité…")
def _load_activity_detail(strava_id: int) -> dict | None:
    """Fetch les streams + le résumé d'une activité Strava (caché 1h)."""
    client_id, client_secret, _ = get_strava_credentials()
    if not (client_id and client_secret):
        return None
    try:
        client = StravaClient.from_tokens_file(client_id, client_secret)
        streams = client.fetch_streams_full(strava_id, _DETAIL_STREAM_KEYS)
        summary = client.fetch_activity_summary(strava_id)
    except StravaAuthError:
        return None
    return {"streams": streams or {}, "summary": summary or {}}


def _render_activity_map(latlng: list[list[float]]) -> None:
    """Trace la trace GPS sur une carte. pydeck si dispo, sinon st.map."""
    coords = [[pt[1], pt[0]] for pt in latlng if pt and len(pt) >= 2]
    if not coords:
        return
    try:
        import pydeck as pdk
        lats = [c[1] for c in coords]
        lons = [c[0] for c in coords]
        view = pdk.ViewState(
            latitude=(min(lats) + max(lats)) / 2,
            longitude=(min(lons) + max(lons)) / 2,
            zoom=12,
            pitch=0,
        )
        layer = pdk.Layer(
            "PathLayer",
            data=[{"path": coords}],
            get_path="path",
            get_color=[252, 76, 2],
            width_scale=1,
            width_min_pixels=4,
            pickable=False,
        )
        st.pydeck_chart(pdk.Deck(
            layers=[layer], initial_view_state=view, map_style=None,
        ))
    except Exception:  # noqa: BLE001 — fallback robuste
        st.map(pd.DataFrame({
            "lat": [c[1] for c in coords],
            "lon": [c[0] for c in coords],
        }))


def _render_activity_detail(strava_id: int) -> None:
    """Vue détail d'une activité : carte GPS, courbes, export GPX."""
    st.divider()
    detail = _load_activity_detail(strava_id)
    if detail is None:
        st.warning(
            "Impossible de charger les détails (credentials Strava absents "
            "ou token expiré). Reconfigurez le `.env` et relancez le flow OAuth."
        )
        return

    streams = detail.get("streams") or {}
    summary = detail.get("summary") or {}
    name = summary.get("name") or f"Activité {strava_id}"
    start_iso = summary.get("start_date")

    st.subheader(f"📍 {name}")
    if summary.get("start_date_local"):
        st.caption(summary["start_date_local"])

    latlng = streams.get("latlng")
    if latlng:
        _render_activity_map(latlng)
    else:
        st.info("Pas de trace GPS pour cette activité (indoor / home-trainer).")

    time_stream = streams.get("time")
    if time_stream:
        time_min = [t / 60 for t in time_stream]
        if "heartrate" in streams:
            st.markdown("**Fréquence cardiaque (bpm)**")
            st.line_chart(pd.DataFrame(
                {"FC": streams["heartrate"]}, index=time_min,
            ).rename_axis(_TIME_AXIS_LABEL))
        if "altitude" in streams:
            st.markdown("**Altitude (m)**")
            st.line_chart(pd.DataFrame(
                {"altitude": streams["altitude"]}, index=time_min,
            ).rename_axis(_TIME_AXIS_LABEL))
        if "watts" in streams:
            st.markdown("**Puissance (W)**")
            st.line_chart(pd.DataFrame(
                {"puissance": streams["watts"]}, index=time_min,
            ).rename_axis(_TIME_AXIS_LABEL))

    cols = st.columns(2)
    with cols[0]:
        if latlng:
            try:
                start_dt = dt.datetime.fromisoformat(
                    (start_iso or "").replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                start_dt = dt.datetime.now(dt.timezone.utc)
            gpx_xml = build_gpx(name, start_dt, streams)
            st.download_button(
                "📥 Télécharger en GPX",
                data=gpx_xml,
                file_name=f"strava_{strava_id}.gpx",
                mime="application/gpx+xml",
                width="stretch",
            )
        else:
            st.button(
                "📥 Télécharger en GPX",
                disabled=True,
                width="stretch",
                help="Trace GPS absente — export GPX impossible.",
            )
    with cols[1]:
        analyze_clicked = st.button(
            "🤖 Analyser cette sortie",
            key=f"analyze_btn_{strava_id}",
            type="primary",
            width="stretch",
        )

    _render_activity_analysis(strava_id, analyze_clicked)


def _render_activity_analysis(strava_id: int, triggered: bool) -> None:
    """Rendu de l'analyse coach inline (lance run_turn au clic, mémorise la réponse)."""
    state_key = f"activity_analysis_{strava_id}"

    if triggered:
        prompt = _ACTIVITY_ANALYSIS_PROMPT.format(strava_id=strava_id)
        with st.spinner("Le coach analyse cette sortie…"):
            try:
                reply = run_turn(prompt)
                st.session_state[state_key] = reply
            except OllamaError as exc:
                st.session_state[state_key] = None
                st.error(f"Erreur Ollama : {exc}")
                return

    reply = st.session_state.get(state_key)
    if reply is None:
        return

    st.markdown("### 🤖 Analyse du coach")
    if reply.content:
        st.markdown(reply.content)
    if reply.thinking:
        with st.expander("🧠 Raisonnement"):
            st.markdown(reply.thinking)
    if reply.tool_trace:
        with st.expander(f"🛠 Tools appelés ({len(reply.tool_trace)})"):
            for trace in reply.tool_trace:
                st.markdown(f"**`{trace.name}`** — args : `{trace.arguments}`")
                st.json(trace.result, expanded=False)


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

    if st.button("🔁 Recalculer la charge", width="stretch",
                 help="Recalcule training_load pour toute la base "
                      "(utile après modification de FTP / HRrepos / HRmax)."):
        try:
            with st.spinner("Recalcul en cours…"):
                updated = recalculate_training_loads()
            st.success(f"{updated} activité(s) mise(s) à jour.")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Erreur de recalcul : {exc}")

    if st.button("📥 Backfill HR max", width="stretch",
                 help="Re-télécharge l'historique Strava pour compléter "
                      "max_heart_rate sur les activités déjà en base."):
        client_id, client_secret, _ = get_strava_credentials()
        if not (client_id and client_secret):
            st.error("STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET absents du .env.")
        else:
            try:
                with st.spinner("Récupération de l'historique…"):
                    client = StravaClient.from_tokens_file(client_id, client_secret)
                    updated = backfill_activity_fields(client)
                st.success(f"{updated} activité(s) complétée(s).")
            except StravaAuthError as exc:
                st.error(str(exc))
            except Exception as exc:  # noqa: BLE001
                st.error(f"Erreur de backfill : {exc}")

    if st.button("📥 Backfill zones HR", width="stretch",
                 help="Télécharge les streams HR pour ventiler chaque activité "
                      "en 5 zones %HRR. 1 appel API par activité — peut être "
                      "long. Idempotent : ne re-traite jamais une activité déjà "
                      "calculée."):
        client_id, client_secret, _ = get_strava_credentials()
        if not (client_id and client_secret):
            st.error("STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET absents du .env.")
        else:
            try:
                with st.spinner("Calcul des zones HR…"):
                    client = StravaClient.from_tokens_file(client_id, client_secret)
                    updated = backfill_hr_zones(client)
                st.success(f"{updated} activité(s) ventilée(s) en zones HR.")
            except StravaAuthError as exc:
                st.error(str(exc))
            except Exception as exc:  # noqa: BLE001
                st.error(f"Erreur de backfill zones : {exc}")


tab_dashboard, tab_morning, tab_coach, tab_plan = st.tabs(
    ["📊 Tableau de bord", "🌅 Matin", "🤖 Coach", "📋 Plan"]
)

# ---- Tab Tableau de bord -----------------------------------------------------

def _render_global_alerts() -> None:
    """Bandeau d'alertes agrégées : surentraînement auto + dérive matinale."""
    overtraining = detect_overtraining_signals()
    morning_alerts = detect_morning_alerts()
    ot_alerts = overtraining.get("alerts") or []
    if not ot_alerts and not morning_alerts:
        return
    with st.container(border=True):
        st.markdown("### 🚨 Signaux d'alerte")
        for alert in ot_alerts:
            st.warning(f"**{alert['indicator']}** — {alert['message']}")
        for alert in morning_alerts:
            metric = alert["metric"]
            severity = alert["severity"]
            arrow = "⬇️" if alert["delta_pct"] < 0 else "⬆️"
            msg = (
                f"**{metric}** {arrow} {alert['delta_pct']:+.1f}% vs baseline "
                f"({alert['latest']:.1f} le {alert['latest_date']})"
            )
            (st.error if severity == "critical" else st.warning)(msg)


def _auto_backfill_sport_types(activities: list[dict]) -> list[dict]:
    """Au premier lancement après l'ajout du champ `sport_type`, complète
    silencieusement la colonne pour les activités historiques. Idempotent
    (la fonction backfill ne fait rien s'il n'y a plus de NULL). Protégé
    par session_state pour ne pas re-tenter à chaque rerun Streamlit.
    """
    needs = any(a.get("sport_type") is None for a in activities)
    if not needs or st.session_state.get("sport_type_backfill_done"):
        return activities

    client_id, client_secret, _ = get_strava_credentials()
    if not (client_id and client_secret):
        st.session_state["sport_type_backfill_done"] = True
        return activities

    try:
        with st.spinner("Migration : récupération des sport_type…"):
            client = StravaClient.from_tokens_file(client_id, client_secret)
            backfill_sport_types(client)
        st.session_state["sport_type_backfill_done"] = True
        return fetch_activities_from_db()
    except Exception:  # noqa: BLE001 — fallback silencieux : le dashboard reste utilisable
        st.session_state["sport_type_backfill_done"] = True
        return activities


with tab_dashboard:
    activities = fetch_activities_from_db()

    if not activities:
        st.warning(
            f"Aucune activité dans la base ({get_db_path()}). "
            "Lancez d'abord `python -m domestique_ai.ingestion.strava_oauth_flow` "
            "ou cliquez sur **Synchroniser Strava** dans la barre latérale."
        )
    else:
        activities = _auto_backfill_sport_types(activities)
        _render_global_alerts()
        df = pd.DataFrame(activities)
        df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None)

        today = dt.date.today()
        curves = pd.DataFrame(calculate_ctl_atl_tsb(activities, end_date=today))
        curves["date"] = pd.to_datetime(curves["date"])

        min_date = df["date"].min().date()
        max_date = max(df["date"].max().date(), today)
        default_start = max(min_date, today - dt.timedelta(days=180))

        date_range = st.sidebar.date_input(
            "Plage de dates",
            value=(default_start, today),
            min_value=min_date,
            max_value=max_date,
        )
        if isinstance(date_range, tuple) and len(date_range) == 2:
            start_date, end_date = date_range
        else:
            start_date, end_date = default_start, today

        start_ts = pd.Timestamp(start_date)
        end_ts = pd.Timestamp(end_date) + pd.Timedelta(days=1)
        df_filtered = df[(df["date"] >= start_ts) & (df["date"] < end_ts)]
        curves_filtered = curves[(curves["date"] >= start_ts) & (curves["date"] < end_ts)]

        # Volume cyclisme : année civile et semaine ISO en cours.
        # Indépendant du filtre « Plage de dates » (sinon « année en cours »
        # n'aurait plus de sens).
        if "sport_type" in df.columns:
            today = pd.Timestamp.now().normalize()
            year_start = pd.Timestamp(today.year, 1, 1)
            week_start = today - pd.Timedelta(days=today.weekday())
            rides = df[df["sport_type"].apply(_is_ride)]
            year_rides = rides[rides["date"] >= year_start]
            week_rides = rides[rides["date"] >= week_start]
            year_distance = float(pd.to_numeric(
                year_rides["distance"], errors="coerce").fillna(0).sum())
            year_duration = float(pd.to_numeric(
                year_rides["duration"], errors="coerce").fillna(0).sum())
            week_distance = float(pd.to_numeric(
                week_rides["distance"], errors="coerce").fillna(0).sum())
            week_duration = float(pd.to_numeric(
                week_rides["duration"], errors="coerce").fillna(0).sum())
            ride_cols = st.columns(4)
            ride_cols[0].metric("Km vélo (année)", _format_km(year_distance))
            ride_cols[1].metric("Temps vélo (année)", _format_hours(year_duration))
            ride_cols[2].metric("Km vélo (semaine)", _format_km(week_distance))
            ride_cols[3].metric("Temps vélo (semaine)", _format_hours(week_duration))

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

        zone_cols = [f"hr_{key}_time" for key in HR_ZONE_KEYS]
        if all(col in df_filtered.columns for col in zone_cols):
            zone_totals = [
                float(pd.to_numeric(df_filtered[col], errors="coerce")
                      .fillna(0).sum())
                for col in zone_cols
            ]
            zone_grand_total = sum(zone_totals)
            if zone_grand_total > 0:
                st.subheader("Répartition par zone HR")
                bpm_ranges = _hr_zone_bpm_ranges(get_hr_rest(), get_hr_max())
                summary = {
                    "Zone": ["Z1", "Z2", "Z3", "Z4", "Z5"],
                    "%HRR": list(_HR_ZONE_HRR_LABELS),
                    "Temps": [_format_hms(t) for t in zone_totals],
                    _HR_ZONE_PCT_COL: [
                        f"{(t / zone_grand_total * 100):.1f} %"
                        for t in zone_totals
                    ],
                }
                if bpm_ranges:
                    summary["Plage BPM"] = bpm_ranges
                    columns = ["Zone", "%HRR", "Plage BPM",
                               "Temps", _HR_ZONE_PCT_COL]
                else:
                    columns = ["Zone", "%HRR", "Temps", _HR_ZONE_PCT_COL]
                st.dataframe(
                    pd.DataFrame(summary)[columns],
                    width="stretch", hide_index=True,
                )

        weights = fetch_weight_history()
        if weights:
            weights_df = pd.DataFrame(weights)
            weights_df["date"] = pd.to_datetime(weights_df["date"])
            weights_filtered = weights_df[
                (weights_df["date"] >= start_ts)
                & (weights_df["date"] < end_ts)
            ]
            if not weights_filtered.empty:
                st.subheader("Évolution du poids")
                st.line_chart(
                    weights_filtered.set_index("date")[["weight"]]
                )

        st.subheader("Détail des activités")
        df_display = df_filtered.sort_values("date", ascending=False).copy()
        if "duration" in df_display.columns:
            df_display["duration"] = (
                pd.to_numeric(df_display["duration"], errors="coerce")
                .fillna(0)
                .astype(int)
                .map(lambda s: f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}")
            )
        if "distance" in df_display.columns:
            df_display["distance"] = (
                pd.to_numeric(df_display["distance"], errors="coerce") / 1000
            ).round(2)
        for zone_col in ("hr_z1_time", "hr_z2_time", "hr_z3_time",
                         "hr_z4_time", "hr_z5_time"):
            if zone_col in df_display.columns:
                df_display[zone_col] = pd.to_numeric(
                    df_display[zone_col], errors="coerce"
                ).map(
                    lambda s: ""
                    if pd.isna(s)
                    else f"{int(s) // 3600:02d}:{(int(s) % 3600) // 60:02d}:{int(s) % 60:02d}"
                )
        event = st.dataframe(
            df_display,
            width="stretch",
            on_select="rerun",
            selection_mode="single-row",
            column_config={
                "distance": st.column_config.NumberColumn(
                    "distance (km)", format="%.2f",
                ),
                "strava_id": None,
            },
        )
        selected_rows = getattr(getattr(event, "selection", None), "rows", [])
        if selected_rows:
            selected = df_display.iloc[selected_rows[0]]
            sid = selected.get("strava_id")
            if sid is not None and not pd.isna(sid):
                _render_activity_detail(int(sid))

# ---- Tab Matin ---------------------------------------------------------------


def _render_morning_form(target_date: dt.date) -> None:
    """Formulaire de saisie quotidienne. Pré-remplit si la date a déjà été saisie."""
    iso = target_date.isoformat()
    existing = fetch_morning_entry(iso) or {}

    with st.form(key=f"morning_form_{iso}"):
        c1, c2, c3 = st.columns(3)
        with c1:
            hrv = st.number_input(
                "HRV (ms)", min_value=0.0, max_value=300.0,
                value=float(existing.get("hrv_ms") or 0.0),
                step=0.5, help="RMSSD au repos (Zepp / mesure manuelle).",
            )
            resting_hr = st.number_input(
                "FC repos (bpm)", min_value=0.0, max_value=120.0,
                value=float(existing.get("resting_hr") or 0.0),
                step=1.0,
            )
        with c2:
            sleep_hours = st.number_input(
                "Sommeil (h)", min_value=0.0, max_value=14.0,
                value=float(existing.get("sleep_hours") or 0.0),
                step=0.25,
            )
            sleep_score = st.number_input(
                "Score sommeil Zepp (0-100)", min_value=0, max_value=100,
                value=int(existing.get("sleep_score") or 0), step=1,
            )
        with c3:
            stress_score = st.number_input(
                "Stress matinal Zepp (0-100)", min_value=0, max_value=100,
                value=int(existing.get("stress_score") or 0), step=1,
            )
            notes = st.text_input(
                "Notes (optionnel)", value=existing.get("notes") or "",
            )

        submitted = st.form_submit_button("💾 Enregistrer", type="primary")
        if submitted:
            save_morning_entry(
                iso,
                hrv_ms=hrv or None,
                resting_hr=resting_hr or None,
                sleep_hours=sleep_hours or None,
                sleep_score=sleep_score or None,
                stress_score=stress_score or None,
                notes=notes.strip() or None,
            )
            st.success(f"Entrée du {iso} enregistrée.")
            st.rerun()


def _render_morning_baselines() -> None:
    """4 metrics avec baseline 14j et delta % de la dernière valeur."""
    metrics_meta = [
        ("hrv_ms", "HRV", "ms"),
        ("resting_hr", "FC repos", "bpm"),
        ("sleep_score", "Score sommeil", "/100"),
        ("stress_score", "Stress", "/100"),
    ]
    cols = st.columns(4)
    for col, (key, label, unit) in zip(cols, metrics_meta, strict=True):
        b = compute_baselines(key)
        if not b.get("available"):
            col.metric(label, "—", help=b.get("reason", ""))
            continue
        delta = f"{b['delta_pct']:+.1f}% vs baseline {b['baseline']:.1f}{unit}"
        col.metric(label, f"{b['latest']:.1f}{unit}", delta=delta,
                   delta_color="inverse" if key in ("resting_hr",
                                                    "stress_score") else "normal")


def _render_morning_charts() -> None:
    """Courbes d'évolution sur 90 jours."""
    history = fetch_morning_history(days=90)
    if not history:
        st.info("Pas encore d'historique. Saisis ta première entrée ci-dessus.")
        return
    df = pd.DataFrame(history)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")

    pairs = [
        ("hrv_ms", "HRV (ms)"),
        ("resting_hr", "FC repos (bpm)"),
        ("sleep_score", "Score sommeil (0-100)"),
        ("stress_score", "Stress matinal (0-100)"),
    ]
    cols = st.columns(2)
    for i, (col_name, title) in enumerate(pairs):
        if col_name in df.columns and df[col_name].notna().any():
            with cols[i % 2]:
                st.markdown(f"**{title}**")
                st.line_chart(df[[col_name]].dropna())


with tab_morning:
    st.subheader("🌅 Métriques matinales")
    st.caption(
        "Saisis chaque matin tes valeurs Zepp / Amazfit pour détecter une "
        "dérive vs ta baseline 14 j (signal classique de surentraînement). "
        "Tous les champs sont optionnels."
    )

    target = st.date_input(
        "Date", value=dt.date.today(), max_value=dt.date.today(),
        key="morning_target_date",
    )
    _render_morning_form(target if isinstance(target, dt.date) else dt.date.today())

    st.divider()
    st.markdown("### Tendances")
    _render_morning_baselines()
    _render_morning_charts()


# ---- Tab Coach ---------------------------------------------------------------


def _ensure_coach_state() -> None:
    if "coach_session_id" not in st.session_state:
        st.session_state.coach_session_id = new_session_id()
    if "coach_history" not in st.session_state:
        st.session_state.coach_history = []
    if "coach_traces" not in st.session_state:
        st.session_state.coach_traces = {}


def _render_coach_message(idx: int, role: str, content: str) -> None:
    with st.chat_message("assistant" if role == "assistant" else "user"):
        if content:
            st.markdown(content)
        trace_data = st.session_state.coach_traces.get(idx)
        if trace_data:
            thinking = trace_data.get("thinking")
            tool_trace = trace_data.get("tool_trace") or []
            if thinking:
                with st.expander("💡 Réflexion du modèle", expanded=False):
                    st.markdown(thinking)
            if tool_trace:
                with st.expander(f"🔧 Outils appelés ({len(tool_trace)})",
                                 expanded=False):
                    for call in tool_trace:
                        st.markdown(f"**{call['name']}**({call['arguments']})")
                        st.json(call["result"])


@st.dialog("Supprimer la conversation ?")
def _confirm_delete_session() -> None:
    st.write(
        "Cette action supprimera définitivement tous les messages de la "
        "conversation courante."
    )
    col_yes, col_no = st.columns(2)
    with col_yes:
        if st.button("Oui, supprimer", type="primary", width="stretch"):
            delete_session(st.session_state.coach_session_id)
            st.session_state.coach_session_id = new_session_id()
            st.session_state.coach_history = []
            st.session_state.coach_traces = {}
            st.rerun()
    with col_no:
        if st.button("Annuler", width="stretch"):
            st.rerun()


with tab_coach:
    _ensure_coach_state()

    st.caption(
        f"Modèle : `{get_ollama_model()}` · "
        f"Session : `{st.session_state.coach_session_id[:8]}…`"
    )

    sessions = list_sessions(limit=20)
    has_persisted = any(
        s["session_id"] == st.session_state.coach_session_id for s in sessions
    )

    col_new, col_delete, col_load = st.columns([1, 1, 3])
    with col_new:
        if st.button("🆕 Nouvelle session", width="stretch"):
            st.session_state.coach_session_id = new_session_id()
            st.session_state.coach_history = []
            st.session_state.coach_traces = {}
            st.rerun()
    with col_delete:
        if st.button("🗑️ Supprimer", width="stretch",
                     disabled=not has_persisted,
                     help="Supprime la conversation courante de l'historique."):
            _confirm_delete_session()
    with col_load:
        labels = {
            s["session_id"]: f"{s['started_at'][:16]} · {s['preview'] or '—'}"
            for s in sessions
        }
        options = ["(session courante)"] + list(labels.keys())
        selection = st.selectbox(
            "Reprendre une conversation",
            options=options,
            format_func=lambda v: "(session courante)" if v == "(session courante)"
            else labels.get(v, v),
            label_visibility="collapsed",
        )
        if (selection != "(session courante)"
                and selection != st.session_state.coach_session_id):
            st.session_state.coach_session_id = selection
            st.session_state.coach_history = [
                {"role": m["role"], "content": m.get("content") or ""}
                for m in load_session(selection)
                if m.get("role") in ("user", "assistant")
                and (m.get("content") or "").strip()
            ]
            st.session_state.coach_traces = {}
            st.rerun()

    for idx, msg in enumerate(st.session_state.coach_history):
        if msg.get("role") not in ("user", "assistant"):
            continue
        _render_coach_message(idx, msg["role"], msg.get("content") or "")

    user_input = st.chat_input("Pose une question au coach…")
    if user_input:
        session_id = st.session_state.coach_session_id
        st.session_state.coach_history.append(
            {"role": "user", "content": user_input}
        )
        append_message(session_id, "user", {"role": "user", "content": user_input})

        try:
            with st.spinner("Le coach réfléchit…"):
                reply = run_turn(
                    user_input,
                    history=[
                        m for m in st.session_state.coach_history[:-1]
                        if m.get("role") in ("user", "assistant")
                    ],
                )
        except OllamaError as exc:
            st.session_state.coach_history.append(
                {"role": "assistant",
                 "content": f"⚠️ Coach indisponible : {exc}"}
            )
        else:
            idx = len(st.session_state.coach_history)
            st.session_state.coach_history.append(
                {"role": "assistant", "content": reply.content}
            )
            st.session_state.coach_traces[idx] = {
                "thinking": reply.thinking,
                "tool_trace": [
                    {
                        "name": tc.name,
                        "arguments": tc.arguments,
                        "result": tc.result,
                    }
                    for tc in reply.tool_trace
                ],
            }
            append_message(
                session_id, "assistant",
                {"role": "assistant", "content": reply.content,
                 "thinking": reply.thinking,
                 "tool_calls": st.session_state.coach_traces[idx]["tool_trace"]},
            )

        st.rerun()


# ---- Tab Plan ----------------------------------------------------------------

_PLAN_KIND_EMOJI = {
    "recovery": "💆",
    "endurance": "🚴",
    "tempo": "⚡",
    "intervals": "🔥",
}


def _format_plan_label(record: dict) -> str:
    created = record["created_at"][:16].replace("T", " ")
    target = record.get("target_date") or "sans objectif"
    weeks = record.get("weeks") or "?"
    return f"{created} · {weeks} sem · objectif {target}"


def _render_plan_calendar(plan: list, hr_rest: float | None,
                          hr_max: float | None) -> None:
    """Affiche le plan sous forme de calendrier hebdomadaire + détails."""
    if not plan:
        st.info("Plan vide.")
        return

    rows = []
    for w in plan:
        d = dt.date.fromisoformat(w.date)
        monday = d - dt.timedelta(days=d.weekday())
        rows.append({
            "Semaine": monday.isoformat(),
            "Date": w.date,
            "Jour": d.strftime("%a"),
            "Type": f"{_PLAN_KIND_EMOJI.get(w.kind, '•')} {w.kind}",
            "Nom": w.name,
            "Durée (min)": w.duration_min,
            "Zone": w.target_zone.upper(),
            "TSS": w.estimated_tss,
        })
    df_plan = pd.DataFrame(rows)
    st.dataframe(df_plan, hide_index=True, width="stretch")

    weekly_tss = df_plan.groupby("Semaine", as_index=False)["TSS"].sum()
    weekly_tss.rename(columns={"TSS": "TSS prévu"}, inplace=True)
    st.markdown("**TSS prévu par semaine**")
    st.bar_chart(weekly_tss.set_index("Semaine"))

    with st.expander("🔍 Voir le détail des séances"):
        for w in plan:
            d = dt.date.fromisoformat(w.date)
            st.markdown(
                f"**{d.strftime('%a %d %b')}** — {w.name} "
                f"(_{w.duration_min} min, {w.target_zone.upper()}, ~{w.estimated_tss} TSS_)"
            )
            for step in w.structure:
                minutes = step.duration_sec // 60
                seconds = step.duration_sec % 60
                duration_label = f"{minutes}'{seconds:02d}\"" if seconds else f"{minutes}'"
                st.markdown(
                    f"  - `{step.phase}` · {step.zone.upper()} · {duration_label}"
                )

    zip_bytes = plan_to_zip(plan, hr_rest=hr_rest, hr_max=hr_max)
    col_dl, col_push = st.columns([1, 1])
    with col_dl:
        st.download_button(
            "📥 Télécharger en `.zip`",
            data=zip_bytes,
            file_name=f"plan_{plan[0].date}_{plan[-1].date}.zip",
            mime="application/zip",
            width="stretch",
            help="Archive locale (fichiers `.FIT` Workout, 1 par séance).",
        )
    with col_push:
        creds_ok = garmin_credentials_present()
        cache_ok = garmin_token_cache_present()
        push_disabled = not creds_ok
        push_help = (
            "Identifiants Garmin absents : ajouter `GARMIN_EMAIL` et "
            "`GARMIN_PASSWORD` dans `.env`."
            if not creds_ok
            else (
                "Aucun cache token : la 1ʳᵉ connexion (avec MFA si activé) "
                "doit être faite en CLI : "
                "`python -m domestique_ai.export.garmin_connect`."
                if not cache_ok
                else "Crée les séances dans Garmin Connect et planifie le calendrier."
            )
        )
        schedule_workouts = st.checkbox(
            "Planifier sur le calendrier",
            value=True,
            help=(
                "Si coché, chaque séance est ajoutée à la date prévue dans le "
                "calendrier Garmin Connect, sinon elle reste seulement dans la "
                "bibliothèque d'entraînements."
            ),
        )
        if st.button(
            "☁️ Pousser sur Garmin Connect",
            disabled=push_disabled,
            help=push_help,
            width="stretch",
            type="secondary",
        ):
            progress_bar = st.progress(0.0, text="Initialisation…")

            def _on_progress(idx, total, workout):
                progress_bar.progress(
                    (idx + 1) / max(1, total),
                    text=f"Upload {idx + 1}/{total} — {workout.name}",
                )

            try:
                client = get_garmin_client()
                results = garmin_push_plan(
                    plan,
                    schedule=schedule_workouts,
                    hr_rest=hr_rest,
                    hr_max=hr_max,
                    client=client,
                    progress=_on_progress,
                )
            except GarminPushError as exc:
                progress_bar.empty()
                st.error(str(exc))
            else:
                progress_bar.empty()
                st.session_state["plan_push_results"] = results

    if st.session_state.get("plan_push_results"):
        results = st.session_state["plan_push_results"]
        ok_count = sum(1 for r in results if r.get("workout_id") and "error" not in r)
        scheduled_count = sum(1 for r in results if r.get("scheduled"))
        failed = [r for r in results if not r.get("workout_id") or r.get("error")]
        if failed:
            st.warning(
                f"{ok_count}/{len(results)} séances créées, "
                f"{scheduled_count} planifiées · {len(failed)} en erreur."
            )
        else:
            st.success(
                f"{ok_count}/{len(results)} séances créées · "
                f"{scheduled_count} planifiées sur le calendrier Garmin Connect."
            )
        with st.expander("Détail des uploads"):
            for r in results:
                line = f"**{r['date']}** — {r['workout']}"
                if r.get("workout_id"):
                    line += f" → [voir sur Garmin]({r['url']})"
                    if r.get("scheduled"):
                        line += " · 📅 planifié"
                if r.get("error"):
                    line += f" · ⚠️ {r['error']}"
                st.markdown(line)


with tab_plan:
    st.markdown(
        "Génère un plan d'entraînement multi-semaines, archive-le en `.zip` "
        "(fichiers `.FIT` Workout) ou pousse-le directement sur **Garmin "
        "Connect** (les séances apparaissent dans la bibliothèque + le calendrier)."
    )

    objective = load_objective()
    if objective is None:
        st.warning(
            "Aucun objectif déclaré : le plan se limitera à 4 semaines. "
            "Copier `data/objective.yaml.example` vers `data/objective.yaml` "
            "pour cibler une date d'épreuve."
        )
    else:
        target_label = objective.date or "(pas de date)"
        st.caption(
            f"Objectif courant : **{objective.type}** · cible **{target_label}** · "
            f"{objective.distance_km or '?'} km / "
            f"{objective.elevation_m or '?'} m D+"
        )

    availability: Availability | None = None
    try:
        availability = load_availability()
    except AvailabilityError as exc:
        st.error(f"`data/availability.yaml` invalide : {exc}")
    if availability is None:
        st.warning(
            "Aucune disponibilité déclarée : le plan utilise la grille par "
            "défaut Lun/Mer/Ven/Dim. Copier "
            "`data/availability.yaml.example` vers `data/availability.yaml` "
            "pour personnaliser jours, durées max et indoor/outdoor."
        )
    else:
        bullets = " · ".join(
            f"**{d.name}** {d.max_duration_min} min ({d.context})"
            for d in availability.days
        )
        st.info(f"Disponibilité chargée : {bullets}")

    with st.container(border=True):
        st.markdown("### Générer un nouveau plan")
        col_sessions, col_focus, col_btn = st.columns([1, 2, 1])
        with col_sessions:
            sessions_per_week = st.number_input(
                "Séances / semaine", min_value=2, max_value=7, value=4, step=1,
                key="plan_sessions_per_week",
            )
        with col_focus:
            focus = st.text_input(
                "Focus (optionnel)",
                placeholder="ex: endurance fond, sprint, montagnes…",
                key="plan_focus",
            )
        with col_btn:
            st.markdown("&nbsp;")
            generate = st.button("Générer", type="primary", width="stretch")

        if generate:
            target_date: dt.date | None = None
            target_event_type = "cyclosportive"
            if objective and objective.date:
                try:
                    target_date = dt.date.fromisoformat(objective.date)
                except ValueError:
                    target_date = None
                target_event_type = objective.type
            try:
                with st.spinner("Calcul de la périodisation…"):
                    activities_for_ctl = fetch_activities_from_db()
                    curves_for_ctl = calculate_ctl_atl_tsb(
                        activities_for_ctl, end_date=dt.date.today()
                    )
                    ctl_now = float(curves_for_ctl[-1]["CTL"]) if curves_for_ctl else 0.0
                    plan = build_training_plan(
                        target_date=target_date,
                        ctl_current=ctl_now,
                        sessions_per_week=int(sessions_per_week),
                        availability=availability,
                        target_event_type=target_event_type,
                        focus=focus or None,
                    )
                    plan_id = save_plan(
                        plan,
                        target_date=target_date,
                        target_event_type=target_event_type,
                        sessions_per_week=int(sessions_per_week),
                    )
                st.success(f"Plan #{plan_id} généré : {len(plan)} séances.")
                st.session_state["plan_selected_id"] = plan_id
            except Exception as exc:  # noqa: BLE001
                st.error(f"Échec de la génération : {exc}")

    plans = list_plans(limit=20)
    if not plans:
        st.info("Aucun plan généré pour l'instant.")
    else:
        default_id = st.session_state.get("plan_selected_id") or plans[0]["id"]
        plan_ids = [p["id"] for p in plans]
        if default_id not in plan_ids:
            default_id = plan_ids[0]
        selected_id = st.selectbox(
            "Plan à afficher",
            options=plan_ids,
            index=plan_ids.index(default_id),
            format_func=lambda pid: _format_plan_label(
                next(p for p in plans if p["id"] == pid)
            ),
        )
        col_show, col_delete = st.columns([4, 1])
        with col_delete:
            if st.button("🗑️ Supprimer ce plan", width="stretch"):
                delete_plan(int(selected_id))
                st.session_state.pop("plan_selected_id", None)
                st.rerun()

        plan_obj = load_plan(int(selected_id))
        if plan_obj:
            _render_plan_calendar(plan_obj, get_hr_rest(), get_hr_max())
