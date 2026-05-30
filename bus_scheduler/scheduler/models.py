from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional


@dataclass
class RouteSegment:
    from_stop: str
    to_stop: str
    distance_km: float


@dataclass
class Route:
    stops: List[str]
    segments: List[RouteSegment]


@dataclass
class Station:
    id: str
    name: str
    num_chargers: int = 1


@dataclass
class Bus:
    id: str
    operator: str
    direction: str  # 'BK' = Bengaluru->Kochi, 'KB' = Kochi->Bengaluru
    departure_minutes: float  # minutes from midnight


@dataclass
class Weights:
    individual: float = 1.0
    operator: float = 1.0
    overall: float = 1.0
    extra: Dict[str, float] = field(default_factory=dict)


@dataclass
class Constants:
    battery_range_km: float = 240.0
    charge_time_min: float = 25.0
    speed_kmph: float = 60.0


@dataclass
class ScenarioMeta:
    id: str
    name: str
    description: str


@dataclass
class Scenario:
    meta: ScenarioMeta
    route: Route
    stations: List[Station]
    buses: List[Bus]
    weights: Weights
    constants: Constants


@dataclass
class ChargingStop:
    station_id: str
    arrival_time: float
    wait_time: float
    charge_start: float
    charge_end: float


@dataclass
class BusTimeline:
    bus_id: str
    operator: str
    direction: str
    departure_time: float
    charging_stops: List[ChargingStop]
    arrival_time: float
    total_wait_time: float


@dataclass
class StationLog:
    station_id: str
    charging_events: List[Dict[str, Any]]


@dataclass
class ScheduleResult:
    scenario_name: str
    bus_timelines: List[BusTimeline]
    station_logs: Dict[str, StationLog]
    total_wait_time: float
    weights_used: Weights
