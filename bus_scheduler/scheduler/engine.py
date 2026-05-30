"""
Core scheduling engine for the Bus Charging Scheduler.

Algorithm:
  Event-driven simulation with weighted priority queuing.
  1. For each bus, compute a greedy charging plan (charge as late as possible).
  2. Simulate the journey: buses arrive at stations, join a queue, and are
     served by priority score when the charger is free.
  3. Priority is computed as a weighted sum of pluggable rule scores
     (see rules.py).

Extending:
  - Add a new rule in rules.py + map its weight key in WEIGHT_MAP below.
  - Increase num_chargers in a station JSON to add more chargers.
  - Add more stops to the route segments without code changes.
"""

import heapq
import json
from collections import defaultdict
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any

from .models import (
    Scenario, ScenarioMeta, Route, RouteSegment, Station, Bus,
    Weights, Constants, ChargingStop, BusTimeline, StationLog, ScheduleResult,
)
from .rules import RULES


# Maps scenario weight keys to rule names registered in rules.py
WEIGHT_MAP = {
    "individual": "individual_wait",
    "operator":   "operator_equity",
    "overall":    "urgency",
}


def _parse_time(t: str) -> float:
    """Convert 'HH:MM' to minutes from midnight."""
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def load_scenario(path: str) -> Scenario:
    """Load and parse a scenario JSON file into a Scenario dataclass."""
    with open(path, "r") as f:
        data = json.load(f)

    meta = ScenarioMeta(**data["meta"])

    route_data = data["route"]
    segments = [RouteSegment(**s) for s in route_data["segments"]]
    route = Route(stops=route_data["stops"], segments=segments)

    stations = [Station(**s) for s in data["stations"]]

    buses = [
        Bus(
            id=b["id"],
            operator=b["operator"],
            direction=b["direction"],
            departure_minutes=_parse_time(b["departure"]),
        )
        for b in data["buses"]
    ]

    w = data["weights"]
    weights = Weights(
        individual=w.get("individual", 1.0),
        operator=w.get("operator", 1.0),
        overall=w.get("overall", 1.0),
        extra={k: v for k, v in w.items() if k not in ("individual", "operator", "overall")},
    )

    c = data["constants"]
    constants = Constants(
        battery_range_km=c.get("battery_range_km", 240.0),
        charge_time_min=c.get("charge_time_min", 25.0),
        speed_kmph=c.get("speed_kmph", 60.0),
    )

    return Scenario(
        meta=meta,
        route=route,
        stations=stations,
        buses=buses,
        weights=weights,
        constants=constants,
    )


def _route_positions(route: Route, direction: str) -> List[Tuple[str, float]]:
    """
    Return [(stop_name, distance_from_origin)] for the given direction.
    direction 'BK' = forward (first stop → last stop)
    direction 'KB' = reverse (last stop → first stop)
    """
    seg_dist: Dict[Tuple[str, str], float] = {}
    for seg in route.segments:
        seg_dist[(seg.from_stop, seg.to_stop)] = seg.distance_km
        seg_dist[(seg.to_stop, seg.from_stop)] = seg.distance_km  # bidirectional

    ordered = route.stops if direction == "BK" else list(reversed(route.stops))

    positions: List[Tuple[str, float]] = []
    dist = 0.0
    for i, stop in enumerate(ordered):
        positions.append((stop, dist))
        if i < len(ordered) - 1:
            key = (ordered[i], ordered[i + 1])
            dist += seg_dist[key]

    return positions


def _find_greedy_plan(
    origin_pos: float,
    dest_pos: float,
    stations_ahead: List[Tuple[str, float]],  # (name, dist) sorted asc
    battery_range: float,
) -> Optional[List[str]]:
    """
    Recursive greedy algorithm: charge at the latest feasible station.
    Returns list of station names (in order), or None if infeasible.
    """
    if dest_pos - origin_pos <= battery_range + 1e-6:
        return []  # Can reach destination without charging

    reachable = [
        (name, pos) for name, pos in stations_ahead
        if origin_pos < pos <= origin_pos + battery_range + 1e-6
    ]

    if not reachable:
        return None  # Infeasible

    for name, pos in reversed(reachable):
        remaining = [(n, p) for n, p in stations_ahead if p > pos + 1e-6]
        sub = _find_greedy_plan(pos, dest_pos, remaining, battery_range)
        if sub is not None:
            return [name] + sub

    return None


class Simulator:
    """
    Event-driven bus charging simulator.

    Events:
      'arrive'         — bus arrives at a charging station
      'charge_complete' — a charger slot at a station becomes free

    For each 'arrive' event the bus joins the station queue.
    For each 'charge_complete' (or a new 'arrive' when charger is idle)
    the highest-priority waiting bus is selected and its charging session
    is scheduled.
    """

    def __init__(self, scenario: Scenario):
        self.scenario = scenario
        self.speed = scenario.constants.speed_kmph
        self.charge_time = scenario.constants.charge_time_min
        self.battery_range = scenario.constants.battery_range_km
        self.weights = scenario.weights

        self.station_ids = {s.id for s in scenario.stations}
        # multi-charger support: one free-time per charger slot
        self.station_chargers: Dict[str, List[float]] = {
            s.id: [0.0] * s.num_chargers for s in scenario.stations
        }
        # queues: list of (arrival_time, counter, bus_id)
        self.station_queue: Dict[str, List[Tuple[float, int, str]]] = {
            s.id: [] for s in scenario.stations
        }

        self.events: List[Tuple] = []  # (time, counter, type, bus_id, station_id)
        self.counter = 0

        self.bus_data: Dict[str, Dict[str, Any]] = {}
        self.operator_total_wait: Dict[str, float] = defaultdict(float)
        self.operator_bus_count: Dict[str, int] = defaultdict(int)

    def run(self) -> ScheduleResult:
        self._init()
        while self.events:
            time, _cnt, etype, bus_id, station_id = heapq.heappop(self.events)
            if etype == "arrive":
                self.station_queue[station_id].append((_cnt, time, bus_id))
                # Note: we store (counter, time, bus_id) so we can sort by arrival
                self._try_start_next(station_id, time)
            elif etype == "charge_complete":
                self._try_start_next(station_id, time)
        return self._build_result()

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _init(self):
        for bus in self.scenario.buses:
            rp = _route_positions(self.scenario.route, bus.direction)
            pos_map = dict(rp)
            origin = rp[0][0]
            destination = rp[-1][0]

            origin_pos = pos_map[origin]
            dest_pos = pos_map[destination]

            stations_ahead = [
                (name, pos) for name, pos in rp
                if name in self.station_ids and pos > origin_pos + 1e-6
            ]

            plan = _find_greedy_plan(origin_pos, dest_pos, stations_ahead, self.battery_range)
            if plan is None:
                raise ValueError(
                    f"No feasible charging plan for bus {bus.id}. "
                    "Check battery range and route distances."
                )

            self.bus_data[bus.id] = {
                "bus": bus,
                "plan": plan,
                "plan_idx": 0,
                "route_pos": rp,
                "pos_map": pos_map,
                "origin": origin,
                "destination": destination,
                "charging_stops": [],
            }

            self.operator_bus_count[bus.operator] += 1

            if plan:
                first = plan[0]
                travel = (pos_map[first] - origin_pos) / self.speed * 60.0
                self._push_arrive(bus.id, first, bus.departure_minutes + travel)

    # ------------------------------------------------------------------
    # Event helpers
    # ------------------------------------------------------------------

    def _push_arrive(self, bus_id: str, station_id: str, time: float):
        heapq.heappush(self.events, (time, self.counter, "arrive", bus_id, station_id))
        self.counter += 1

    def _push_charge_complete(self, station_id: str, time: float):
        heapq.heappush(self.events, (time, self.counter, "charge_complete", None, station_id))
        self.counter += 1

    # ------------------------------------------------------------------
    # Core scheduling logic
    # ------------------------------------------------------------------

    def _try_start_next(self, station_id: str, current_time: float):
        """If a charger is free and buses are waiting, schedule the highest-priority one."""
        chargers = self.station_chargers[station_id]
        earliest_free_idx = min(range(len(chargers)), key=lambda i: chargers[i])
        free_at = chargers[earliest_free_idx]

        if free_at > current_time + 1e-6:
            return  # All chargers still busy

        # Collect buses that have arrived
        arrived = [
            (arr_t, cnt, bid)
            for cnt, arr_t, bid in self.station_queue[station_id]
            if arr_t <= current_time + 1e-6
        ]
        if not arrived:
            return

        # Sort by priority descending
        arrived.sort(key=lambda x: -self._priority(x[2], x[0], current_time))
        arr_t, _cnt, bus_id = arrived[0]

        # Remove from queue (match by bus_id)
        self.station_queue[station_id] = [
            (c, t, bid)
            for c, t, bid in self.station_queue[station_id]
            if bid != bus_id
        ]

        charge_start = max(arr_t, free_at)
        charge_end = charge_start + self.charge_time
        wait = charge_start - arr_t

        self.bus_data[bus_id]["charging_stops"].append({
            "station": station_id,
            "arrival": arr_t,
            "wait": wait,
            "charge_start": charge_start,
            "charge_end": charge_end,
        })

        chargers[earliest_free_idx] = charge_end
        self.operator_total_wait[self.bus_data[bus_id]["bus"].operator] += wait

        # Schedule next station arrival
        data = self.bus_data[bus_id]
        plan_idx = data["plan_idx"] + 1
        data["plan_idx"] = plan_idx

        if plan_idx < len(data["plan"]):
            next_station = data["plan"][plan_idx]
            from_dist = data["pos_map"][station_id]
            to_dist = data["pos_map"][next_station]
            travel = (to_dist - from_dist) / self.speed * 60.0
            self._push_arrive(bus_id, next_station, charge_end + travel)

        self._push_charge_complete(station_id, charge_end)

    # ------------------------------------------------------------------
    # Priority computation (delegates to registered rules)
    # ------------------------------------------------------------------

    def _priority(self, bus_id: str, arrival_time: float, current_time: float) -> float:
        w = self.weights
        weight_map: Dict[str, float] = {
            "individual_wait": w.individual,
            "operator_equity": w.operator,
            "urgency":         w.overall,
        }
        # Include any extra custom weights
        weight_map.update(w.extra)

        total = 0.0
        for rule_name, rule_fn in RULES.items():
            wt = weight_map.get(rule_name, 0.0)
            if wt != 0.0:
                total += wt * rule_fn(
                    bus_id, arrival_time, current_time, self.bus_data, self
                )
        return total

    # ------------------------------------------------------------------
    # Result construction
    # ------------------------------------------------------------------

    def _build_result(self) -> ScheduleResult:
        bus_timelines: List[BusTimeline] = []

        for bus in self.scenario.buses:
            data = self.bus_data[bus.id]
            stops = data["charging_stops"]

            if stops:
                last = stops[-1]
                from_dist = data["pos_map"][last["station"]]
                to_dist = data["pos_map"][data["destination"]]
                travel = (to_dist - from_dist) / self.speed * 60.0
                arrival = last["charge_end"] + travel
            else:
                from_dist = data["pos_map"][data["origin"]]
                to_dist = data["pos_map"][data["destination"]]
                travel = (to_dist - from_dist) / self.speed * 60.0
                arrival = bus.departure_minutes + travel

            total_wait = sum(s["wait"] for s in stops)

            charging_stops = [
                ChargingStop(
                    station_id=s["station"],
                    arrival_time=s["arrival"],
                    wait_time=s["wait"],
                    charge_start=s["charge_start"],
                    charge_end=s["charge_end"],
                )
                for s in stops
            ]

            bus_timelines.append(
                BusTimeline(
                    bus_id=bus.id,
                    operator=bus.operator,
                    direction=bus.direction,
                    departure_time=bus.departure_minutes,
                    charging_stops=charging_stops,
                    arrival_time=arrival,
                    total_wait_time=total_wait,
                )
            )

        # Station logs
        station_logs: Dict[str, StationLog] = {}
        for station in self.scenario.stations:
            events = []
            for bus_id, data in self.bus_data.items():
                bus = data["bus"]
                for stop in data["charging_stops"]:
                    if stop["station"] == station.id:
                        events.append({
                            "bus_id": bus_id,
                            "operator": bus.operator,
                            "direction": bus.direction,
                            "arrival_time": stop["arrival"],
                            "wait_time": stop["wait"],
                            "charge_start": stop["charge_start"],
                            "charge_end": stop["charge_end"],
                        })
            events.sort(key=lambda e: e["charge_start"])
            station_logs[station.id] = StationLog(
                station_id=station.id, charging_events=events
            )

        return ScheduleResult(
            scenario_name=self.scenario.meta.name,
            bus_timelines=bus_timelines,
            station_logs=station_logs,
            total_wait_time=sum(tl.total_wait_time for tl in bus_timelines),
            weights_used=self.scenario.weights,
        )
