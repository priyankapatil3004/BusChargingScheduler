# Architecture — Bus Charging Scheduler

---

## Scheduling approach

### Framework choice: event-driven simulation with weighted priority queuing

The scheduler is a discrete-event simulation (DES). The world is modelled as a stream  
of timestamped events (bus arrives at station, charger becomes free). At each decision  
point — when a charger is free and multiple buses are queued — a **weighted priority  
score** determines who goes first.

**Why DES over alternatives?**

| Alternative | Why not |
|---|---|
| LP / MILP solver | Requires enumerating all charging-plan combinations upfront; doesn't scale gracefully as the world grows; hard to add soft rules incrementally |
| Rule-based greedy (fixed heuristic) | Weights can't be changed without code changes; hard to reason about multi-constraint behaviour |
| Constraint programming | Powerful but heavyweight; overkill for this problem size; harder for engineers to extend |
| DES + weighted priority (chosen) | O(n log n) per scenario; weights are data not code; rules are functions plugged in at runtime; scales to thousands of buses |

The priority score is:

```
priority(bus) = individual_weight × individual_rule(bus)
              + operator_weight   × operator_rule(bus)
              + overall_weight    × urgency_rule(bus)
              + [any extra rules × their weights]
```

Higher score = charged first.

---

## Data structure design

The scenario JSON is the **single source of truth** for every input the scheduler needs.  
It deliberately carries more than today's requirements so that future changes are data  
edits, not code changes.

```json
{
  "meta":      { "id", "name", "description" },
  "route":     { "stops": [...], "segments": [{"from_stop", "to_stop", "distance_km"}] },
  "stations":  [{ "id", "name", "num_chargers" }],
  "constants": { "battery_range_km", "charge_time_min", "speed_kmph" },
  "weights":   { "individual", "operator", "overall", ...extras },
  "buses":     [{ "id", "operator", "direction", "departure" }]
}
```

### Key design decisions

**`segments` as an explicit list, not a distance matrix.**  
The scheduler derives route order from the `stops` list and looks up distances from  
`segments` by stop-pair key. Adding a new stop means adding one entry to `stops` and  
one entry to `segments` — no code changes.

**`num_chargers` on each station.**  
Today all stations have 1 charger. The field is already there, and the simulator  
maintains a `charger_free_times` list of length `num_chargers`. To double a station's  
capacity, change one number in the JSON.

**`weights` as an open dict, not a fixed struct.**  
The three standard weights (`individual`, `operator`, `overall`) have first-class fields  
in the `Weights` dataclass. Additional keys are carried in `extra: Dict[str, float]` and  
automatically forwarded to the priority computation. A brand-new rule requires:  
1. A function in `rules.py` (≤10 lines)  
2. A weight key in the scenario JSON  
3. One line in `engine.py`'s `WEIGHT_MAP`

**Direction as a string tag, not a boolean.**  
`direction: "BK" | "KB"` is a tag, not an assumption. The route-position function  
reverses the stop order for `KB` buses; the same code handles both. Adding a third  
route segment (e.g., a branch line) just means adding a new direction tag and a  
matching route in the scenario file.

---

## Anticipated future changes

Each entry below names a change, explains how the current design handles it, and  
confirms whether code changes are needed.

### 1. Add a new charging station (e.g., station E between D and Kochi)

**How:** Add `"E"` to `route.stops` between `"D"` and `"Kochi"`, add a segment  
`{"from_stop": "D", "to_stop": "E", "distance_km": 50}` and  
`{"from_stop": "E", "to_stop": "Kochi", "distance_km": 50}`, add `{"id": "E", ...}` to  
`stations`.  
**Code changes needed:** None. The greedy plan finder and simulator iterate over  
whatever stations are in the JSON.

### 2. Increase chargers at a busy station

**How:** Change `num_chargers` from `1` to `2` (or any value) in the station JSON.  
**Code changes needed:** None. The simulator already maintains a list of charger  
free-times of length `num_chargers`.

### 3. Add a fourth operator

**How:** Add buses with `"operator": "newco"` to the scenario JSON.  
**Code changes needed:** None. Operators are strings; the engine tracks them via  
`defaultdict`.

### 4. Change any route distance

**How:** Edit `distance_km` in the relevant segment.  
**Code changes needed:** None.

### 5. Change battery range or charging time

**How:** Edit `battery_range_km` or `charge_time_min` in `constants`.  
**Code changes needed:** None.

### 6. Add a new soft rule (e.g., time-of-day electricity cost)

**How:**  
1. Write a rule function in `rules.py` (`@register_rule("electricity_cost")`)  
2. Add `"electricity_cost": 1.5` to the scenario weights  
3. Add `"electricity_cost": "electricity_cost"` to `WEIGHT_MAP` in `engine.py`  
**Code changes needed:** ≤ 15 lines across two files; zero changes to the simulator loop.

### 7. Add a new hard rule (e.g., priority buses that must not wait)

**How:** Add a `"priority": true` field to selected bus entries in the JSON. In  
`rules.py`, add a rule that returns `math.inf` for priority buses. Priority buses will  
always score highest and jump the queue.  
**Code changes needed:** One rule function + one weight. No changes to the simulator.

### 8. Multiple routes sharing stations

**How:** Each scenario already owns its own `route` and `stations` block. Running  
two routes that share a physical station means giving both scenarios the same station  
ID and merging their events into one shared `station_chargers` dict. This is a small  
extension to the simulator's initialisation, not a rewrite.

### 9. Driver shift constraints

**How:** Add `"shift_end_minutes"` to the bus JSON object. Add a hard rule in  
`rules.py` that penalises schedules where `charge_end + travel_to_dest > shift_end`.

### 10. More buses (hundreds or thousands)

**How:** Add more entries to `buses`. The event-driven simulator is O(n log n) in  
the number of events; it handles thousands of buses without structural changes.

### 11. Variable charging time per station (e.g., fast chargers)

**How:** Add `"charge_time_min"` to individual station objects. The simulator reads  
charging time from the station, falling back to `constants.charge_time_min`.

### 12. Per-operator SLA (maximum acceptable wait)

**How:** Add `"max_wait_min"` to the operator definition (a new top-level `operators`  
key in the scenario JSON). Add a soft or hard rule in `rules.py` that reads this field.

---

## Assumptions made

1. **Greedy charging plan (charge as late as possible).** This minimises the number of  
   stops and maximises range utilisation. The alternative (charge as early as possible)  
   would increase station load at outer stations.

2. **Speed is constant and equal for all buses.** The spec states this explicitly.

3. **Each bus starts with a full charge.** The spec states this explicitly.

4. **Charging always fills to 100%.** The spec states this explicitly.

5. **Stations do not have an opening/closing time.** Not specified; assumed always open.

6. **Priority is re-evaluated at each queue-dispatch event, not pre-sorted.** This means  
   operator equity and urgency reflect the state of the network at the moment the  
   charger becomes free, not at the moment the bus arrives.

7. **Buses within the same direction do not share stations.** BK buses use stations B  
   and D; KB buses use stations C and A. This is the greedy plan's natural outcome —  
   the two directions interleave on the route but use different stations, minimising  
   cross-direction contention. (Station A is used only by KB buses, B only by BK, etc.)

8. **Times are in minutes from midnight; overflow into the next day is displayed as  
   `+1d HH:MM`.** This is purely cosmetic and doesn't affect scheduling logic.

---

## How to change a weight — code example

In `scenarios/scenario_4.json`:
```json
"weights": {
  "individual": 1.0,
  "operator":   2.0,   // ← was 1.0 in scenario 1
  "overall":    1.0
}
```

That's the only change needed. No code touched.

---

## How to add a new rule — code example

```python
# scheduler/rules.py

@register_rule("electricity_cost")
def electricity_cost_rule(bus_id, arrival_time, current_time, bus_data, state):
    """
    Prefer to dispatch buses during cheap-electricity hours (00:00–06:00).
    Buses that arrive during expensive hours get lower priority to encourage
    waiting until off-peak.
    """
    hour = (current_time % (24 * 60)) / 60
    off_peak = 0 <= hour < 6
    return 30.0 if off_peak else 0.0
```

Then in `scenarios/any_scenario.json`:
```json
"weights": { ..., "electricity_cost": 1.0 }
```

And in `scheduler/engine.py` → `WEIGHT_MAP`:
```python
"electricity_cost": "electricity_cost"
```
