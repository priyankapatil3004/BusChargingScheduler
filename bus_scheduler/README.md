# Bus Charging Scheduler

A Python + Streamlit app that schedules electric bus charging along the route  
**Bengaluru → A → B → C → D → Kochi** (540 km, 240 km battery range).

---

## Running locally

```bash
cd bus_scheduler
pip install -r requirements.txt
streamlit run app.py
```

The app opens at `http://localhost:5000`.

---

## How to change a weight

Weights live **only** in the scenario JSON file — one value, one place.

Example: raise the operator-equity weight to 3.0 in Scenario 4.

```json
// scenarios/scenario_4.json
"weights": {
  "individual": 1.0,
  "operator":   3.0,   // ← change this
  "overall":    1.0
}
```

Reload the app and re-run the scenario — the new weights are picked up automatically.

The three built-in weight keys map to rules as follows:

| Weight key    | Rule              | Effect when increased                                  |
|---------------|-------------------|--------------------------------------------------------|
| `individual`  | `individual_wait` | Buses that have waited longer get higher priority      |
| `operator`    | `operator_equity` | Operators with more accumulated delay get priority     |
| `overall`     | `urgency`         | Buses closer to their range limit get priority first   |

---

## How to add a new rule

1. Open `scheduler/rules.py`.
2. Define a function and decorate it with `@register_rule("your_rule_name")`:

```python
@register_rule("peak_hours")
def peak_hours_rule(bus_id, arrival_time, current_time, bus_data, state):
    """Give higher priority to buses arriving during peak hours (18:00–22:00)."""
    peak_start, peak_end = 18 * 60, 22 * 60
    arr = arrival_time % (24 * 60)
    return 50.0 if peak_start <= arr <= peak_end else 0.0
```

3. Add the weight to any scenario JSON:

```json
"weights": {
  "individual": 1.0,
  "operator":   1.0,
  "overall":    1.0,
  "peak_hours": 1.5
}
```

4. Wire the weight key in `scheduler/engine.py` → `WEIGHT_MAP`:

```python
WEIGHT_MAP = {
    "individual": "individual_wait",
    "operator":   "operator_equity",
    "overall":    "urgency",
    "peak_hours": "peak_hours",   # ← add this line
}
```

That's it — no changes to the simulation engine.

---

## How to encode a new scenario

Create a new JSON file in `scenarios/`. The format is fully self-describing:

```json
{
  "meta": { "id": "scenario_6", "name": "...", "description": "..." },
  "route": {
    "stops": ["Bengaluru", "A", "B", "C", "D", "Kochi"],
    "segments": [
      {"from_stop": "Bengaluru", "to_stop": "A", "distance_km": 100},
      ...
    ]
  },
  "stations": [
    {"id": "A", "name": "Station A", "num_chargers": 1},
    ...
  ],
  "constants": { "battery_range_km": 240, "charge_time_min": 25, "speed_kmph": 60 },
  "weights": { "individual": 1.0, "operator": 1.0, "overall": 1.0 },
  "buses": [
    {"id": "bus-BK-01", "operator": "kpn", "direction": "BK", "departure": "19:00"},
    ...
  ]
}
```

The app discovers all `scenarios/*.json` files automatically on startup.

---

## Project structure

```
bus_scheduler/
├── app.py                  # Streamlit UI
├── requirements.txt
├── .streamlit/config.toml
├── scheduler/
│   ├── __init__.py
│   ├── models.py           # Dataclasses: Scenario, Bus, Station, …
│   ├── engine.py           # Event-driven simulator + scenario loader
│   └── rules.py            # Pluggable priority rules
└── scenarios/
    ├── scenario_1.json     # Even spacing
    ├── scenario_2.json     # Bunched start
    ├── scenario_3.json     # Asymmetric load
    ├── scenario_4.json     # Operator-heavy (operator weight = 2.0)
    └── scenario_5.json     # Worst-case convergence
```
