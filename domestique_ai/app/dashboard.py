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
from domestique_ai.ingestion.strava import (
    StravaAuthError,
    StravaClient,
    backfill_activity_fields,
    backfill_hr_zones,
    sync_activities,
)
from domestique_ai.llm.coach import run_turn
from domestique_ai.llm.conversations import (
    append_message,
    delete_session,
    list_sessions,
    load_session,
    new_session_id,
)
from domestique_ai.llm.ollama_client import OllamaError
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
    "les chiffres (durée, distance, FC, charge, zones HR). "
    "Étape 2 : appelle `get_training_load_state` (CTL/ATL/TSB du jour) et "
    "`get_objective` pour le contexte. "
    "Étape 3 : conclus en 4-6 lignes en répondant explicitement à : "
    "(1) ce que cette sortie a apporté physiologiquement (filière dominante, "
    "stimulus principal) ; "
    "(2) si elle a été *productive* ou *contre-productive* vu le TSB courant et "
    "l'objectif ; "
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


tab_dashboard, tab_morning, tab_coach = st.tabs(
    ["📊 Tableau de bord", "🌅 Matin", "🤖 Coach"]
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


with tab_dashboard:
    activities = fetch_activities_from_db()

    if not activities:
        st.warning(
            f"Aucune activité dans la base ({get_db_path()}). "
            "Lancez d'abord `python -m domestique_ai.ingestion.strava_oauth_flow` "
            "ou cliquez sur **Synchroniser Strava** dans la barre latérale."
        )
    else:
        _render_global_alerts()
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
