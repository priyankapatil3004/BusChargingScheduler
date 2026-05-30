"""
Bus Charging Scheduler — Streamlit app

Pick a scenario → see the input → see what the scheduler decided.
"""

import os
import glob
import pandas as pd
import streamlit as st

from scheduler import load_scenario, Simulator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fmt_time(minutes: float) -> str:
    """Format float minutes-from-midnight as HH:MM, or +Nd HH:MM for overflow."""
    minutes = int(round(minutes))
    days = minutes // (24 * 60)
    remainder = minutes % (24 * 60)
    h = remainder // 60
    m = remainder % 60
    if days > 0:
        return f"+{days}d {h:02d}:{m:02d}"
    return f"{h:02d}:{m:02d}"


def direction_label(d: str) -> str:
    return "Bengaluru → Kochi" if d == "BK" else "Kochi → Bengaluru"


def operator_badge(op: str) -> str:
    return op.upper()


# ---------------------------------------------------------------------------
# Scenario discovery
# ---------------------------------------------------------------------------

SCENARIOS_DIR = os.path.join(os.path.dirname(__file__), "scenarios")

@st.cache_data
def load_all_scenarios():
    files = sorted(glob.glob(os.path.join(SCENARIOS_DIR, "*.json")))
    scenarios = {}
    for f in files:
        sc = load_scenario(f)
        scenarios[sc.meta.name] = sc
    return scenarios


@st.cache_data
def run_scenario(scenario_name: str):
    scenarios = load_all_scenarios()
    sc = scenarios[scenario_name]
    sim = Simulator(sc)
    return sim.run(), sc


# ---------------------------------------------------------------------------
# Page layout
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Bus Charging Scheduler",
    page_icon="🚌",
    layout="wide",
)

st.title("Bus Charging Scheduler")
st.caption("Bengaluru → A → B → C → D → Kochi  |  540 km  |  240 km battery range")

scenarios = load_all_scenarios()
scenario_names = list(scenarios.keys())

selected = st.selectbox("Select scenario", scenario_names, index=0)

result, sc = run_scenario(selected)

tab_input, tab_buses, tab_stations = st.tabs([
    "Scenario Input",
    "Bus Timetables",
    "Station View",
])


# ===========================================================================
# Tab 1 — Scenario Input
# ===========================================================================

with tab_input:
    st.subheader(sc.meta.name)
    st.write(sc.meta.description)

    col_c, col_w = st.columns(2)

    with col_c:
        st.markdown("**Physical constants**")
        st.table(pd.DataFrame([{
            "Battery range": f"{sc.constants.battery_range_km:.0f} km",
            "Charge time": f"{sc.constants.charge_time_min:.0f} min",
            "Speed": f"{sc.constants.speed_kmph:.0f} km/h",
        }]).T.rename(columns={0: "Value"}))

    with col_w:
        st.markdown("**Optimisation weights**")
        w = sc.weights
        weight_rows = {
            "Individual bus": w.individual,
            "Operator equity": w.operator,
            "Overall efficiency": w.overall,
        }
        weight_rows.update({k: v for k, v in w.extra.items()})
        st.table(pd.DataFrame(weight_rows, index=["Weight"]).T)

    st.markdown("---")
    st.markdown("**Departure schedule**")

    bus_rows = []
    for b in sc.buses:
        bus_rows.append({
            "Bus ID": b.id,
            "Operator": operator_badge(b.operator),
            "Direction": direction_label(b.direction),
            "Departure": fmt_time(b.departure_minutes),
        })
    bus_df = pd.DataFrame(bus_rows)
    st.dataframe(bus_df, hide_index=True)


# ===========================================================================
# Tab 2 — Bus Timetables
# ===========================================================================

with tab_buses:
    st.subheader("Per-bus timetable")
    st.caption(
        "Each row is one charging stop. "
        "Greedy charging plan: charge as late as possible to minimise stops."
    )

    rows = []
    for tl in result.bus_timelines:
        for i, stop in enumerate(tl.charging_stops):
            rows.append({
                "Bus": tl.bus_id,
                "Operator": operator_badge(tl.operator),
                "Direction": direction_label(tl.direction),
                "Departs": fmt_time(tl.departure_time),
                "Stop #": i + 1,
                "Station": stop.station_id,
                "Arrives station": fmt_time(stop.arrival_time),
                "Wait (min)": f"{stop.wait_time:.0f}",
                "Charge start": fmt_time(stop.charge_start),
                "Charge end": fmt_time(stop.charge_end),
                "Arrives dest.": fmt_time(tl.arrival_time),
                "Total wait (min)": f"{tl.total_wait_time:.0f}",
            })

    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df, hide_index=True)
    else:
        st.info("No charging stops computed.")

    st.markdown("---")
    st.markdown("**Summary**")

    summary_rows = []
    for tl in result.bus_timelines:
        unimpeded = tl.departure_time + (
            (sc.route.segments[0].distance_km
             + sum(s.distance_km for s in sc.route.segments))
            / sc.constants.speed_kmph * 60
            + len(tl.charging_stops) * sc.constants.charge_time_min
        )
        summary_rows.append({
            "Bus": tl.bus_id,
            "Operator": operator_badge(tl.operator),
            "Direction": direction_label(tl.direction),
            "Departs": fmt_time(tl.departure_time),
            "Arrives": fmt_time(tl.arrival_time),
            "Charging stops": ", ".join(s.station_id for s in tl.charging_stops),
            "Total wait (min)": f"{tl.total_wait_time:.0f}",
        })

    summary_df = pd.DataFrame(summary_rows)
    st.dataframe(summary_df, hide_index=True)

    st.metric(
        "Total wait across all buses",
        f"{result.total_wait_time:.0f} min",
        help="Sum of all queuing wait time across every bus and every station.",
    )


# ===========================================================================
# Tab 3 — Station View
# ===========================================================================

with tab_stations:
    st.subheader("Per-station charging order")
    st.caption("Buses are listed in the order they actually used each charger.")

    station_ids = [s.id for s in sc.stations]

    cols = st.columns(len(station_ids))
    for col, sid in zip(cols, station_ids):
        with col:
            log = result.station_logs.get(sid)
            st.markdown(f"### Station {sid}")
            station_obj = next(s for s in sc.stations if s.id == sid)
            st.caption(f"{station_obj.num_chargers} charger(s)")

            if log and log.charging_events:
                rows = []
                for rank, ev in enumerate(log.charging_events, 1):
                    rows.append({
                        "#": rank,
                        "Bus": ev["bus_id"],
                        "Op.": operator_badge(ev["operator"]),
                        "Dir.": "BK" if ev["direction"] == "BK" else "KB",
                        "Arrives": fmt_time(ev["arrival_time"]),
                        "Wait": f"{ev['wait_time']:.0f}m",
                        "Start": fmt_time(ev["charge_start"]),
                        "End": fmt_time(ev["charge_end"]),
                    })
                st.dataframe(pd.DataFrame(rows), hide_index=True)
            else:
                st.info("No buses charged here.")
