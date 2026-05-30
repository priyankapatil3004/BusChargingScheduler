"""
Pluggable priority rules for the scheduler.

Each rule is a function with the signature:
    rule(bus_id, arrival_time, current_time, bus_data, state) -> float

A higher score means higher priority (the bus gets to charge sooner).

To add a new rule:
1. Define a function and decorate it with @register_rule("rule_name")
2. Add the corresponding weight key to the scenario's weights dict
3. Map the weight in engine.py's WEIGHT_MAP

The engine accumulates: priority = sum(weight_i * rule_i(...))
"""

from typing import Callable, Dict, Any

RuleFn = Callable[[str, float, float, Dict, Any], float]

RULES: Dict[str, RuleFn] = {}


def register_rule(name: str) -> Callable:
    """Decorator to register a priority rule by name."""
    def decorator(fn: RuleFn) -> RuleFn:
        RULES[name] = fn
        return fn
    return decorator


@register_rule("individual_wait")
def individual_wait_rule(
    bus_id: str,
    arrival_time: float,
    current_time: float,
    bus_data: Dict,
    state: Any,
) -> float:
    """
    Buses that have been waiting longer get higher priority.
    Addresses: no single bus should wait too long.
    """
    return current_time - arrival_time


@register_rule("operator_equity")
def operator_equity_rule(
    bus_id: str,
    arrival_time: float,
    current_time: float,
    bus_data: Dict,
    state: Any,
) -> float:
    """
    Operators whose buses have accumulated more wait time get priority
    for their next bus. Addresses: each operator's fleet should run
    smoothly as a group.
    """
    operator = bus_data[bus_id]["bus"].operator
    op_wait = state.operator_total_wait.get(operator, 0.0)
    op_count = max(1, state.operator_bus_count.get(operator, 1))
    return op_wait / op_count


@register_rule("urgency")
def urgency_rule(
    bus_id: str,
    arrival_time: float,
    current_time: float,
    bus_data: Dict,
    state: Any,
) -> float:
    """
    Buses that have driven farther since their last charge (closer to
    range limit) get higher priority. Addresses: overall network
    efficiency — avoid stranded buses.
    """
    data = bus_data[bus_id]
    pos_map = data["pos_map"]
    origin = data["origin"]
    plan = data["plan"]
    plan_idx = data["plan_idx"]

    if plan_idx > 0:
        prev_dist = pos_map[plan[plan_idx - 1]]
    else:
        prev_dist = pos_map[origin]

    if plan_idx < len(plan):
        curr_dist = pos_map[plan[plan_idx]]
    else:
        curr_dist = prev_dist

    driven = curr_dist - prev_dist
    return driven


# ---------------------------------------------------------------------------
# Example of how to add a new rule (not active by default):
#
# @register_rule("priority_operator")
# def priority_operator_rule(bus_id, arrival_time, current_time, bus_data, state):
#     """Give a fixed bonus to a specific high-priority operator."""
#     VIP_OPERATORS = {"kpn"}
#     operator = bus_data[bus_id]["bus"].operator
#     return 100.0 if operator in VIP_OPERATORS else 0.0
#
# Then add  "priority_operator": 0.0  to the weights in any scenario JSON.
# ---------------------------------------------------------------------------
