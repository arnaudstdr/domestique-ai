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

_HR_ZONE_HRR_LABELS = ("< 60 %", "60–70 %", "70–80 %", "80–90 %", "≥ 90 %")
_HR_ZONE_HRR_BOUNDS = ((0.0, 0.60), (0.60, 0.70), (0.70, 0.80),
                       (0.80, 0.90), (0.90, 1.0))
_HR_ZONE_PCT_COL = "% du total"

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


tab_dashboard, tab_coach = st.tabs(["📊 Tableau de bord", "🤖 Coach"])

# ---- Tab Tableau de bord -----------------------------------------------------

with tab_dashboard:
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
        st.dataframe(
            df_display,
            width="stretch",
            column_config={
                "distance": st.column_config.NumberColumn("distance (km)", format="%.2f"),
            },
        )

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


with tab_coach:
    _ensure_coach_state()

    st.caption(
        f"Modèle : `{get_ollama_model()}` · "
        f"Session : `{st.session_state.coach_session_id[:8]}…`"
    )

    col_new, col_load = st.columns([1, 3])
    with col_new:
        if st.button("🆕 Nouvelle session", width="stretch"):
            st.session_state.coach_session_id = new_session_id()
            st.session_state.coach_history = []
            st.session_state.coach_traces = {}
            st.rerun()
    with col_load:
        sessions = list_sessions(limit=20)
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
