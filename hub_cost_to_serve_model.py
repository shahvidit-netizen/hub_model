"""
hub_cost_to_serve_model.py

Purpose
-------
Network design tool to evaluate hub investment decisions on TOTAL end-to-end
cost-to-serve, not just middle-mile savings. For each market cluster the
model prices out every leg of the journey:

    ORIGIN --(linehaul)--> [HUB sort touch]* --(linehaul)--> MARKET (last mile)

and rolls it up into a single Cost Per Parcel (CPP) and a total dollar
cost-to-serve figure (CPP x volume). It then lets you toggle a hub in or
out of the network and see the system-level (and market-level) cost delta.

Key modeling ideas
------------------
1. Linehaul cost is volume-sensitive (trailer fill), not just distance-based.
   Consolidating markets through a hub can IMPROVE linehaul CPP even after
   adding a sort touch, because it improves trailer utilization on the
   upstream lane. This is the "real value across the full arc" the tool is
   built to surface -- middle-mile-only analysis misses this.
2. Sort touch cost is a flat $/parcel charge applied at every intermediate
   hub node a parcel passes through.
3. Last-mile CPP is driven by delivery classification (In-Region / Out-of-
   Region) AND by which hub is physically feeding that market -- a hub
   repositioned closer to a market cluster shortens the final stem and can
   lower last-mile CPP. OOR/IR is an INPUT to the last-mile cost table, not
   the organizing frame of the analysis -- the frame is total CPP.
4. Route selection is solved iteratively (a simple best-response / network
   equilibrium heuristic): each market picks its cheapest available route
   given current lane volumes, lane volumes are re-aggregated, and the
   process repeats until it stabilizes. This captures the interaction
   between "which markets use a lane" and "how cheap that lane is."

The file is self-contained and runnable: `python3 hub_cost_to_serve_model.py`
builds a demo network, prints a cost-to-serve report, ranks markets by cost
burden, and shows the system-level impact of toggling a candidate hub on
and off.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import pandas as pd


# --------------------------------------------------------------------------
# Core network primitives
# --------------------------------------------------------------------------

@dataclass
class Lane:
    """A directed transportation link between two network nodes."""
    from_node: str
    to_node: str
    distance_miles: float
    rate_per_mile: float           # $ per mile, per trailer/load
    trailer_capacity: int = 200    # parcels per trailer load
    min_trailers: int = 1          # frequency floor (dispatched regardless of fill)

    def trailers_needed(self, volume: int) -> int:
        if volume <= 0:
            return 0
        return max(self.min_trailers, math.ceil(volume / self.trailer_capacity))

    def linehaul_total_cost(self, volume: int) -> float:
        trailers = self.trailers_needed(volume)
        return trailers * self.distance_miles * self.rate_per_mile

    def linehaul_cpp(self, volume: int) -> float:
        if volume <= 0:
            return 0.0
        return self.linehaul_total_cost(volume) / volume

    def utilization(self, volume: int) -> float:
        trailers = self.trailers_needed(volume)
        if trailers == 0:
            return 0.0
        return volume / (trailers * self.trailer_capacity)


@dataclass
class Hub:
    """A sort/cross-dock facility. Can be toggled active/inactive to model
    opening, closing, or repositioning a hub investment."""
    node_id: str
    name: str
    sort_cost_per_parcel: float
    active: bool = True


@dataclass
class Market:
    """A delivery market cluster -- the destination end of the network."""
    market_id: str
    name: str
    origin_node: str
    volume: int
    delivery_class: str                       # e.g. "IR" or "OOR"
    current_path: List[str]                    # nodes incl. origin_node ... market_id
    last_mile_multiplier: float = 1.0           # default last-mile adjustment
    last_mile_multiplier_by_hub: Dict[str, float] = field(default_factory=dict)
    # ^ optional: override last_mile_multiplier when a specific hub is the
    #   last node before the market (models a hub being geographically
    #   closer/farther from this market's delivery stations).


# --------------------------------------------------------------------------
# The network model
# --------------------------------------------------------------------------

class HubNetworkModel:
    def __init__(self, last_mile_rate_table: Dict[str, float]):
        """
        last_mile_rate_table: base $/parcel last-mile cost by delivery class,
        e.g. {"IR": 2.10, "OOR": 3.85}
        """
        self.lanes: Dict[Tuple[str, str], Lane] = {}
        self.hubs: Dict[str, Hub] = {}
        self.markets: Dict[str, Market] = {}
        self.last_mile_rate_table = last_mile_rate_table
        self._adj: Dict[str, List[str]] = {}

    # ---- network construction -------------------------------------------------

    def add_lane(self, lane: Lane) -> None:
        self.lanes[(lane.from_node, lane.to_node)] = lane
        self._adj.setdefault(lane.from_node, []).append(lane.to_node)

    def add_hub(self, hub: Hub) -> None:
        self.hubs[hub.node_id] = hub

    def add_market(self, market: Market) -> None:
        self.markets[market.market_id] = market

    # ---- leg costing ------------------------------------------------------

    def last_mile_cpp(self, market: Market, serving_hub: Optional[str]) -> float:
        base = self.last_mile_rate_table[market.delivery_class]
        if serving_hub is not None and serving_hub in market.last_mile_multiplier_by_hub:
            mult = market.last_mile_multiplier_by_hub[serving_hub]
        else:
            mult = market.last_mile_multiplier
        return base * mult

    def _serving_hub(self, path: List[str]) -> Optional[str]:
        """The last hub a parcel touches before reaching the market node, if any."""
        if len(path) >= 2 and path[-2] in self.hubs:
            return path[-2]
        return None

    def route_is_active(self, path: List[str]) -> bool:
        """A route is usable only if every intermediate hub is active and
        every lane along it exists."""
        for node in path[1:-1]:
            hub = self.hubs.get(node)
            if hub is not None and not hub.active:
                return False
        for a, b in zip(path, path[1:]):
            if (a, b) not in self.lanes:
                return False
        return True

    # ---- route enumeration -------------------------------------------------

    def enumerate_routes(self, origin: str, dest_market_node: str, max_hops: int = 3) -> List[List[str]]:
        """DFS over the lane graph, simulating every feasible route (up to
        max_hops intermediate legs) between an origin and a market node,
        respecting which hubs are currently active."""
        routes: List[List[str]] = []

        def dfs(node: str, path: List[str]):
            if len(path) - 1 > max_hops + 1:
                return
            if node == dest_market_node and len(path) > 1:
                routes.append(list(path))
                return
            for nxt in self._adj.get(node, []):
                if nxt in path:
                    continue
                hub = self.hubs.get(nxt)
                if hub is not None and not hub.active and nxt != dest_market_node:
                    continue
                path.append(nxt)
                dfs(nxt, path)
                path.pop()

        dfs(origin, [origin])
        return routes

    # ---- costing a specific route given current lane volumes --------------

    def route_cost_breakdown(
        self, market: Market, path: List[str], lane_volumes: Dict[Tuple[str, str], int]
    ) -> Dict[str, float]:
        linehaul_cpp = 0.0
        for a, b in zip(path, path[1:]):
            lane = self.lanes[(a, b)]
            vol = lane_volumes.get((a, b), market.volume)
            linehaul_cpp += lane.linehaul_cpp(vol)

        sort_cpp = 0.0
        for node in path[1:-1]:
            hub = self.hubs.get(node)
            if hub is not None:
                sort_cpp += hub.sort_cost_per_parcel

        serving_hub = self._serving_hub(path)
        last_mile = self.last_mile_cpp(market, serving_hub)

        total = linehaul_cpp + sort_cpp + last_mile
        return {
            "linehaul_cpp": round(linehaul_cpp, 4),
            "sort_cpp": round(sort_cpp, 4),
            "last_mile_cpp": round(last_mile, 4),
            "total_cpp": round(total, 4),
        }

    # ---- lane volume aggregation given a full market->path assignment -----

    def _lane_volumes(self, assignment: Dict[str, List[str]]) -> Dict[Tuple[str, str], int]:
        volumes: Dict[Tuple[str, str], int] = {}
        for market_id, path in assignment.items():
            vol = self.markets[market_id].volume
            for a, b in zip(path, path[1:]):
                volumes[(a, b)] = volumes.get((a, b), 0) + vol
        return volumes

    # ---- iterative route optimization (network equilibrium heuristic) -----

    def optimize_assignment(
        self, max_hops: int = 3, iterations: int = 8
    ) -> Dict[str, List[str]]:
        """Each market starts on its current path, then repeatedly re-picks
        its cheapest available route given the lane volumes implied by the
        rest of the network, until the assignment stabilizes (or the
        iteration budget runs out). This is what lets hub consolidation
        benefits show up: a market re-routing onto a hub lane makes that
        lane cheaper for every other market already using it, and vice versa.
        """
        assignment: Dict[str, List[str]] = {
            mid: list(m.current_path) for mid, m in self.markets.items() if self.route_is_active(m.current_path)
        }
        # seed anything whose current path is no longer valid (e.g. hub just
        # got deactivated) with any feasible route for now
        for mid, m in self.markets.items():
            if mid not in assignment:
                candidates = self.enumerate_routes(m.origin_node, m.market_id, max_hops)
                candidates = [p for p in candidates if self.route_is_active(p)]
                if candidates:
                    assignment[mid] = candidates[0]

        route_cache: Dict[str, List[List[str]]] = {}

        for _ in range(iterations):
            lane_volumes = self._lane_volumes(assignment)
            new_assignment: Dict[str, List[str]] = {}
            changed = False

            for mid, market in self.markets.items():
                if mid not in route_cache:
                    all_routes = self.enumerate_routes(market.origin_node, market.market_id, max_hops)
                    route_cache[mid] = [p for p in all_routes if self.route_is_active(p)]
                candidates = [p for p in route_cache[mid] if self.route_is_active(p)]
                if not candidates:
                    # no feasible route at all (shouldn't happen if graph is connected)
                    new_assignment[mid] = assignment.get(mid, market.current_path)
                    continue

                best_path, best_cost = None, math.inf
                for path in candidates:
                    # marginal costing: remove this market's own volume from
                    # the lane, then see what the lane looks like with it added
                    breakdown = self.route_cost_breakdown(market, path, lane_volumes)
                    if breakdown["total_cpp"] < best_cost:
                        best_cost = breakdown["total_cpp"]
                        best_path = path

                new_assignment[mid] = best_path
                if best_path != assignment.get(mid):
                    changed = True

            assignment = new_assignment
            if not changed:
                break

        return assignment

    # ---- reporting ----------------------------------------------------------

    def report(self, assignment: Dict[str, List[str]]) -> pd.DataFrame:
        """Build a cost-to-serve report for whichever markets are present in
        `assignment`. `assignment` may be a partial mapping (e.g. only
        markets whose current_path is still valid under today's active hub
        set) -- markets missing from it are simply left out of the report
        rather than raising a KeyError."""
        lane_volumes = self._lane_volumes(assignment)
        rows = []
        for mid, path in assignment.items():
            market = self.markets[mid]
            breakdown = self.route_cost_breakdown(market, path, lane_volumes)
            rows.append({
                "market_id": mid,
                "market_name": market.name,
                "delivery_class": market.delivery_class,
                "volume": market.volume,
                "route": " -> ".join(path),
                "linehaul_cpp": breakdown["linehaul_cpp"],
                "sort_cpp": breakdown["sort_cpp"],
                "last_mile_cpp": breakdown["last_mile_cpp"],
                "total_cpp": breakdown["total_cpp"],
                "total_cost": round(breakdown["total_cpp"] * market.volume, 2),
            })
        df = pd.DataFrame(rows)
        if not df.empty:
            df = df.sort_values("total_cost", ascending=False).reset_index(drop=True)
        return df

    def system_totals(self, df: pd.DataFrame) -> Dict[str, float]:
        if df.empty:
            return {"total_volume": 0, "total_cost": 0.0, "network_avg_cpp": 0.0}
        total_volume = df["volume"].sum()
        total_cost = df["total_cost"].sum()
        return {
            "total_volume": int(total_volume),
            "total_cost": round(total_cost, 2),
            "network_avg_cpp": round(total_cost / total_volume, 4) if total_volume else 0.0,
        }

    # ---- the bonus feature: toggle a hub and see the system delta ---------

    def toggle_hub(self, hub_id: str, active: bool, max_hops: int = 3, iterations: int = 8):
        """Flip a hub active/inactive, re-optimize routing, and return:
          - baseline report/totals (hub in its ORIGINAL state)
          - scenario report/totals (hub in the NEW state)
          - a market-level delta table
          - the system-level cost delta
        Restores the hub's original state before returning so repeated
        toggles are independent (no side effects across calls).
        """
        if hub_id not in self.hubs:
            raise KeyError(f"Unknown hub_id: {hub_id}")

        original_state = self.hubs[hub_id].active

        # baseline: current state, optimized routing
        baseline_assignment = self.optimize_assignment(max_hops, iterations)
        baseline_df = self.report(baseline_assignment)
        baseline_totals = self.system_totals(baseline_df)

        # scenario: toggled state, optimized routing
        self.hubs[hub_id].active = active
        scenario_assignment = self.optimize_assignment(max_hops, iterations)
        scenario_df = self.report(scenario_assignment)
        scenario_totals = self.system_totals(scenario_df)

        # restore
        self.hubs[hub_id].active = original_state

        merged = baseline_df.merge(
            scenario_df, on=["market_id", "market_name", "delivery_class", "volume"],
            suffixes=("_baseline", "_scenario")
        )
        merged["route_changed"] = merged["route_baseline"] != merged["route_scenario"]
        merged["cpp_delta"] = (merged["total_cpp_scenario"] - merged["total_cpp_baseline"]).round(4)
        merged["cost_delta"] = (merged["total_cost_scenario"] - merged["total_cost_baseline"]).round(2)
        merged = merged.sort_values("cost_delta").reset_index(drop=True)

        system_delta = {
            "total_cost_baseline": baseline_totals["total_cost"],
            "total_cost_scenario": scenario_totals["total_cost"],
            "total_cost_delta": round(scenario_totals["total_cost"] - baseline_totals["total_cost"], 2),
            "network_avg_cpp_baseline": baseline_totals["network_avg_cpp"],
            "network_avg_cpp_scenario": scenario_totals["network_avg_cpp"],
            "markets_rerouted": int(merged["route_changed"].sum()),
        }

        return {
            "baseline_report": baseline_df,
            "scenario_report": scenario_df,
            "market_delta": merged,
            "system_delta": system_delta,
        }


# --------------------------------------------------------------------------
# Demo network + walkthrough
# --------------------------------------------------------------------------

def build_demo_network() -> HubNetworkModel:
    model = HubNetworkModel(last_mile_rate_table={"IR": 2.10, "OOR": 3.85})

    # Hubs: one existing regional hub, one candidate hub (start inactive)
    model.add_hub(Hub("HUB_MEM", "Memphis Regional Hub", sort_cost_per_parcel=0.35, active=True))
    model.add_hub(Hub("HUB_DAL", "Dallas Candidate Hub", sort_cost_per_parcel=0.32, active=False))

    # Lanes: origin -> hub, hub -> hub, hub -> market, origin -> market (direct/bypass)
    model.add_lane(Lane("FC_ATL", "HUB_MEM", distance_miles=390, rate_per_mile=2.60, trailer_capacity=220))
    model.add_lane(Lane("FC_ATL", "HUB_DAL", distance_miles=450, rate_per_mile=2.60, trailer_capacity=220))
    model.add_lane(Lane("HUB_MEM", "HUB_DAL", distance_miles=430, rate_per_mile=2.55, trailer_capacity=220))

    # Direct (no-hub) bypass lanes from origin straight to a market
    for m, dist in [("MKT_SHV", 355), ("MKT_TUL", 470), ("MKT_OKC", 590),
                     ("MKT_LIT", 340), ("MKT_FTW", 730), ("MKT_ABI", 850)]:
        model.add_lane(Lane("FC_ATL", m, distance_miles=dist, rate_per_mile=2.60, trailer_capacity=220))

    # Hub -> market stem lanes (final leg into the market cluster)
    hub_market_stems = {
        "HUB_MEM": {"MKT_SHV": 300, "MKT_TUL": 360, "MKT_LIT": 140, "MKT_OKC": 470},
        "HUB_DAL": {"MKT_SHV": 190, "MKT_TUL": 260, "MKT_OKC": 205, "MKT_FTW": 35, "MKT_ABI": 155, "MKT_LIT": 330},
    }
    for hub, stems in hub_market_stems.items():
        for m, dist in stems.items():
            model.add_lane(Lane(hub, m, distance_miles=dist, rate_per_mile=2.20, trailer_capacity=180))

    # Markets: origin, volume, delivery class, CURRENT real-world path, last-mile tuning
    model.add_market(Market(
        "MKT_SHV", "Shreveport Cluster", origin_node="FC_ATL", volume=1400, delivery_class="OOR",
        current_path=["FC_ATL", "MKT_SHV"],   # currently routed direct, bypassing any hub
        last_mile_multiplier=1.05,
        last_mile_multiplier_by_hub={"HUB_DAL": 0.90, "HUB_MEM": 0.98},
    ))
    model.add_market(Market(
        "MKT_TUL", "Tulsa Cluster", origin_node="FC_ATL", volume=1150, delivery_class="OOR",
        current_path=["FC_ATL", "HUB_MEM", "MKT_TUL"],
        last_mile_multiplier=1.10,
        last_mile_multiplier_by_hub={"HUB_DAL": 0.95, "HUB_MEM": 1.02},
    ))
    model.add_market(Market(
        "MKT_OKC", "Oklahoma City Cluster", origin_node="FC_ATL", volume=2200, delivery_class="IR",
        current_path=["FC_ATL", "MKT_OKC"],
        last_mile_multiplier=1.00,
        last_mile_multiplier_by_hub={"HUB_DAL": 0.92, "HUB_MEM": 1.00},
    ))
    model.add_market(Market(
        "MKT_LIT", "Little Rock Cluster", origin_node="FC_ATL", volume=1900, delivery_class="IR",
        current_path=["FC_ATL", "HUB_MEM", "MKT_LIT"],
        last_mile_multiplier=0.95,
        last_mile_multiplier_by_hub={"HUB_MEM": 0.90, "HUB_DAL": 1.05},
    ))
    model.add_market(Market(
        "MKT_FTW", "Fort Worth Cluster", origin_node="FC_ATL", volume=2600, delivery_class="IR",
        current_path=["FC_ATL", "MKT_FTW"],
        last_mile_multiplier=1.00,
        last_mile_multiplier_by_hub={"HUB_DAL": 0.80, "HUB_MEM": 1.05},
    ))
    model.add_market(Market(
        "MKT_ABI", "Abilene Cluster", origin_node="FC_ATL", volume=800, delivery_class="OOR",
        current_path=["FC_ATL", "MKT_ABI"],
        last_mile_multiplier=1.15,
        last_mile_multiplier_by_hub={"HUB_DAL": 0.88, "HUB_MEM": 1.10},
    ))

    return model


def pretty_print(title: str, df: pd.DataFrame):
    print(f"\n{'=' * 90}\n{title}\n{'=' * 90}")
    print(df.to_string(index=False))


if __name__ == "__main__":
    model = build_demo_network()

    # ---- 1. Current state, priced as-is (no re-optimization) --------------
    current_assignment = {mid: m.current_path for mid, m in model.markets.items()}
    current_df = model.report(current_assignment)
    current_totals = model.system_totals(current_df)
    pretty_print("CURRENT STATE -- end-to-end CPP by market (as currently routed)", current_df)
    print(f"\nSystem totals (current routing): {current_totals}")

    print("\nHighest end-to-end cost burden markets (by total $ cost-to-serve):")
    print(current_df.nlargest(3, "total_cost")[["market_id", "market_name", "total_cpp", "total_cost"]]
          .to_string(index=False))

    # ---- 2. Best achievable routing under TODAY's active hub set ----------
    optimized_assignment = model.optimize_assignment()
    optimized_df = model.report(optimized_assignment)
    optimized_totals = model.system_totals(optimized_df)
    pretty_print("OPTIMIZED ROUTING -- best routes with only HUB_MEM active", optimized_df)
    print(f"\nSystem totals (optimized, HUB_MEM only): {optimized_totals}")

    # ---- 3. Bonus: toggle the candidate hub (HUB_DAL) ON and see the delta ----
    result = model.toggle_hub("HUB_DAL", active=True)
    pretty_print("SCENARIO -- HUB_DAL activated (optimized routing)", result["scenario_report"])
    pretty_print("MARKET-LEVEL DELTA -- HUB_DAL toggled ON vs current active-hub baseline", result["market_delta"][
        ["market_id", "market_name", "route_baseline", "route_scenario", "route_changed",
         "cpp_delta", "cost_delta"]
    ])
    print(f"\nSYSTEM-LEVEL DELTA (HUB_DAL ON): {result['system_delta']}")

    # ---- Save outputs ----
    current_df.to_csv("/mnt/user-data/outputs/current_state_cost_to_serve.csv", index=False)
    optimized_df.to_csv("/mnt/user-data/outputs/optimized_routing_cost_to_serve.csv", index=False)
    result["market_delta"].to_csv("/mnt/user-data/outputs/hub_dal_toggle_delta.csv", index=False)
    print("\nSaved: current_state_cost_to_serve.csv, optimized_routing_cost_to_serve.csv, hub_dal_toggle_delta.csv")
