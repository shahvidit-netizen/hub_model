"""
hub_cost_to_serve_app.py

Streamlit app: compare BASELINE (direct FC -> market) vs OPTIMIZED
(best route per market: direct vs. through an active hub) cost-to-serve.

Run:  streamlit run hub_cost_to_serve_app.py
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st


# --------------------------------------------------------------------------
# Core primitives
# --------------------------------------------------------------------------

@dataclass
class Lane:
    from_node: str
    to_node: str
    distance_miles: float
    rate_per_mile: float
    trailer_capacity: int = 200
    min_trailers: int = 1

    def trailers_needed(self, volume: int) -> int:
        if volume <= 0:
            return 0
        return max(self.min_trailers, math.ceil(volume / self.trailer_capacity))

    def linehaul_total_cost(self, volume: int) -> float:
        return self.trailers_needed(volume) * self.distance_miles * self.rate_per_mile

    def linehaul_cpp(self, volume: int) -> float:
        if volume <= 0:
            return 0.0
        return self.linehaul_total_cost(volume) / volume

    def utilization(self, volume: int) -> float:
        trailers = self.trailers_needed(volume)
        return 0.0 if trailers == 0 else volume / (trailers * self.trailer_capacity)


@dataclass
class Hub:
    node_id: str
    name: str
    sort_cost_per_parcel: float
    active: bool = True


@dataclass
class Market:
    market_id: str
    name: str
    origin_node: str
    volume: int
    delivery_class: str
    last_mile_multiplier: float = 1.0
    last_mile_multiplier_by_hub: Dict[str, float] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Network model
# --------------------------------------------------------------------------

class HubNetworkModel:
    def __init__(self, last_mile_rate_table: Dict[str, float]):
        self.lanes: Dict[Tuple[str, str], Lane] = {}
        self.hubs: Dict[str, Hub] = {}
        self.markets: Dict[str, Market] = {}
        self.last_mile_rate_table = last_mile_rate_table
        self._adj: Dict[str, List[str]] = {}

    # ---- construction ----
    def add_lane(self, lane: Lane) -> None:
        self.lanes[(lane.from_node, lane.to_node)] = lane
        self._adj.setdefault(lane.from_node, []).append(lane.to_node)

    def add_hub(self, hub: Hub) -> None:
        self.hubs[hub.node_id] = hub

    def add_market(self, market: Market) -> None:
        self.markets[market.market_id] = market

    # ---- leg costing ----
    def last_mile_cpp(self, market: Market, serving_hub: Optional[str]) -> float:
        base = self.last_mile_rate_table[market.delivery_class]
        if serving_hub is not None and serving_hub in market.last_mile_multiplier_by_hub:
            mult = market.last_mile_multiplier_by_hub[serving_hub]
        else:
            mult = market.last_mile_multiplier
        return base * mult

    def _serving_hub(self, path: List[str]) -> Optional[str]:
        if len(path) >= 2 and path[-2] in self.hubs:
            return path[-2]
        return None

    def route_is_active(self, path: List[str]) -> bool:
        for node in path[1:-1]:
            hub = self.hubs.get(node)
            if hub is not None and not hub.active:
                return False
        for a, b in zip(path, path[1:]):
            if (a, b) not in self.lanes:
                return False
        return True

    def enumerate_routes(self, origin: str, dest: str, max_hops: int = 3) -> List[List[str]]:
        routes: List[List[str]] = []

        def dfs(node: str, path: List[str]):
            if len(path) - 1 > max_hops + 1:
                return
            if node == dest and len(path) > 1:
                routes.append(list(path))
                return
            for nxt in self._adj.get(node, []):
                if nxt in path:
                    continue
                hub = self.hubs.get(nxt)
                if hub is not None and not hub.active and nxt != dest:
                    continue
                path.append(nxt)
                dfs(nxt, path)
                path.pop()

        dfs(origin, [origin])
        return routes

    def route_cost_breakdown(
        self, market: Market, path: List[str], lane_volumes: Dict[Tuple[str, str], int]
    ) -> Dict[str, float]:
        linehaul_cpp = 0.0
        for a, b in zip(path, path[1:]):
            lane = self.lanes[(a, b)]
            vol = lane_volumes.get((a, b), market.volume)
            linehaul_cpp += lane.linehaul_cpp(vol)

        sort_cpp = sum(
            self.hubs[node].sort_cost_per_parcel
            for node in path[1:-1] if node in self.hubs
        )
        last_mile = self.last_mile_cpp(market, self._serving_hub(path))
        total = linehaul_cpp + sort_cpp + last_mile
        return {
            "linehaul_cpp": round(linehaul_cpp, 4),
            "sort_cpp": round(sort_cpp, 4),
            "last_mile_cpp": round(last_mile, 4),
            "total_cpp": round(total, 4),
        }

    def _lane_volumes(self, assignment: Dict[str, List[str]]) -> Dict[Tuple[str, str], int]:
        volumes: Dict[Tuple[str, str], int] = {}
        for mid, path in assignment.items():
            vol = self.markets[mid].volume
            for a, b in zip(path, path[1:]):
                volumes[(a, b)] = volumes.get((a, b), 0) + vol
        return volumes

    # ---- BASELINE: every market ships DIRECT FC -> market ----
    def baseline_assignment(self) -> Dict[str, List[str]]:
        assignment = {}
        for mid, m in self.markets.items():
            direct = [m.origin_node, m.market_id]
            if (m.origin_node, m.market_id) in self.lanes:
                assignment[mid] = direct
            else:
                # fall back to any feasible route if no direct lane exists
                cands = [p for p in self.enumerate_routes(m.origin_node, m.market_id)
                         if self.route_is_active(p)]
                assignment[mid] = cands[0] if cands else direct
        return assignment

    # ---- OPTIMIZED: iterative best-response route selection ----
    def optimize_assignment(self, max_hops: int = 3, iterations: int = 8) -> Dict[str, List[str]]:
        # seed with baseline (direct) routing
        assignment = self.baseline_assignment()
        route_cache: Dict[str, List[List[str]]] = {}

        for _ in range(iterations):
            lane_volumes = self._lane_volumes(assignment)
            new_assignment: Dict[str, List[str]] = {}
            changed = False

            for mid, market in self.markets.items():
                if mid not in route_cache:
                    route_cache[mid] = [
                        p for p in self.enumerate_routes(market.origin_node, market.market_id, max_hops)
                    ]
                candidates = [p for p in route_cache[mid] if self.route_is_active(p)]
                if not candidates:
                    new_assignment[mid] = assignment.get(mid, [market.origin_node, market.market_id])
                    continue

                best_path, best_cost = None, math.inf
                for path in candidates:
                    breakdown = self.route_cost_breakdown(market, path, lane_volumes)
                    if breakdown["total_cpp"] < best_cost:
                        best_cost, best_path = breakdown["total_cpp"], path

                new_assignment[mid] = best_path
                if best_path != assignment.get(mid):
                    changed = True

            assignment = new_assignment
            if not changed:
                break
        return assignment

    # ---- reporting ----
    def report(self, assignment: Dict[str, List[str]]) -> pd.DataFrame:
        lane_volumes = self._lane_volumes(assignment)
        rows = []
        for mid, market in self.markets.items():
            path = assignment[mid]
            b = self.route_cost_breakdown(market, path, lane_volumes)
            uses_hub = any(n in self.hubs for n in path[1:-1])
            rows.append({
                "market_id": mid,
                "market_name": market.name,
                "delivery_class": market.delivery_class,
                "volume": market.volume,
                "route": " -> ".join(path),
                "via_hub": "Hub" if uses_hub else "Direct",
                "linehaul_cpp": b["linehaul_cpp"],
                "sort_cpp": b["sort_cpp"],
                "last_mile_cpp": b["last_mile_cpp"],
                "total_cpp": b["total_cpp"],
                "total_cost": round(b["total_cpp"] * market.volume, 2),
            })
        return pd.DataFrame(rows).sort_values("total_cost", ascending=False).reset_index(drop=True)

    def system_totals(self, df: pd.DataFrame) -> Dict[str, float]:
        vol = df["volume"].sum()
        cost = df["total_cost"].sum()
        return {
            "total_volume": int(vol),
            "total_cost": round(cost, 2),
            "network_avg_cpp": round(cost / vol, 4) if vol else 0.0,
        }


# --------------------------------------------------------------------------
# Demo network builder (parameterized)
# --------------------------------------------------------------------------

def build_network(ir_rate: float, oor_rate: float,
                  mem_sort: float, dal_sort: float,
                  mem_active: bool, dal_active: bool,
                  rate_per_mile: float, stem_rate: float,
                  trailer_cap: int) -> HubNetworkModel:
    model = HubNetworkModel(last_mile_rate_table={"IR": ir_rate, "OOR": oor_rate})

    model.add_hub(Hub("HUB_MEM", "Memphis Regional Hub", mem_sort, active=mem_active))
    model.add_hub(Hub("HUB_DAL", "Dallas Candidate Hub", dal_sort, active=dal_active))

    # origin -> hub lanes
    model.add_lane(Lane("FC_ATL", "HUB_MEM", 390, rate_per_mile, trailer_cap))
    model.add_lane(Lane("FC_ATL", "HUB_DAL", 450, rate_per_mile, trailer_cap))
    model.add_lane(Lane("HUB_MEM", "HUB_DAL", 430, rate_per_mile * 0.98, trailer_cap))

    # direct bypass lanes
    for m, dist in [("MKT_SHV", 355), ("MKT_TUL", 470), ("MKT_OKC", 590),
                    ("MKT_LIT", 340), ("MKT_FTW", 730), ("MKT_ABI", 850)]:
        model.add_lane(Lane("FC_ATL", m, dist, rate_per_mile, trailer_cap))

    # hub -> market stems
    hub_market_stems = {
        "HUB_MEM": {"MKT_SHV": 300, "MKT_TUL": 360, "MKT_LIT": 140, "MKT_OKC": 470},
        "HUB_DAL": {"MKT_SHV": 190, "MKT_TUL": 260, "MKT_OKC": 205, "MKT_FTW": 35,
                    "MKT_ABI": 155, "MKT_LIT": 330},
    }
    for hub, stems in hub_market_stems.items():
        for m, dist in stems.items():
            model.add_lane(Lane(hub, m, dist, stem_rate, max(1, int(trailer_cap * 0.82))))

    markets = [
        ("MKT_SHV", "Shreveport Cluster", 1400, "OOR", 1.05, {"HUB_DAL": 0.90, "HUB_MEM": 0.98}),
        ("MKT_TUL", "Tulsa Cluster", 1150, "OOR", 1.10, {"HUB_DAL": 0.95, "HUB_MEM": 1.02}),
        ("MKT_OKC", "Oklahoma City Cluster", 2200, "IR", 1.00, {"HUB_DAL": 0.92, "HUB_MEM": 1.00}),
        ("MKT_LIT", "Little Rock Cluster", 1900, "IR", 0.95, {"HUB_MEM": 0.90, "HUB_DAL": 1.05}),
        ("MKT_FTW", "Fort Worth Cluster", 2600, "IR", 1.00, {"HUB_DAL": 0.80, "HUB_MEM": 1.05}),
        ("MKT_ABI", "Abilene Cluster", 800, "OOR", 1.15, {"HUB_DAL": 0.88, "HUB_MEM": 1.10}),
    ]
    for mid, name, vol, cls, mult, by_hub in markets:
        model.add_market(Market(mid, name, "FC_ATL", vol, cls, mult, by_hub))

    return model


# --------------------------------------------------------------------------
# Streamlit UI
# --------------------------------------------------------------------------

st.set_page_config(page_title="Hub Cost-to-Serve Model", layout="wide")
st.title("Hub Cost-to-Serve: Baseline vs. Optimized")
st.caption("Baseline = every market ships direct FC → market. "
           "Optimized = best route per market (direct vs. through an active hub) "
           "with volume-sensitive linehaul.")

with st.sidebar:
    st.header("Network Parameters")

    st.subheader("Last-mile base rate ($/parcel)")
    ir_rate = st.number_input("In-Region (IR)", 0.5, 20.0, 2.10, 0.05)
    oor_rate = st.number_input("Out-of-Region (OOR)", 0.5, 20.0, 3.85, 0.05)

    st.subheader("Linehaul")
    rate_per_mile = st.number_input("Linehaul $/mile/trailer", 0.5, 10.0, 2.60, 0.05)
    stem_rate = st.number_input("Hub→market stem $/mile", 0.5, 10.0, 2.20, 0.05)
    trailer_cap = st.slider("Trailer capacity (parcels)", 50, 400, 220, 10)

    st.subheader("Hub sort cost ($/parcel)")
    mem_sort = st.number_input("Memphis sort cost", 0.0, 5.0, 0.35, 0.01)
    dal_sort = st.number_input("Dallas sort cost", 0.0, 5.0, 0.32, 0.01)

    st.subheader("Hub Filter (include / exclude)")
    mem_active = st.toggle("Memphis Regional Hub active", value=True)
    dal_active = st.toggle("Dallas Candidate Hub active", value=True)

    st.subheader("Solver")
    max_hops = st.slider("Max intermediate hops", 1, 4, 3)
    iterations = st.slider("Equilibrium iterations", 1, 20, 8)

    run = st.button("Run Baseline & Optimize", type="primary", use_container_width=True)


def render(model: HubNetworkModel):
    baseline_df = model.report(model.baseline_assignment())
    baseline_tot = model.system_totals(baseline_df)

    optimized_df = model.report(model.optimize_assignment(max_hops, iterations))
    optimized_tot = model.system_totals(optimized_df)

    cost_delta = round(optimized_tot["total_cost"] - baseline_tot["total_cost"], 2)
    cpp_delta = round(optimized_tot["network_avg_cpp"] - baseline_tot["network_avg_cpp"], 4)
    savings_pct = (-cost_delta / baseline_tot["total_cost"] * 100) if baseline_tot["total_cost"] else 0

    # --- KPI row ---
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Baseline total cost", f"${baseline_tot['total_cost']:,.0f}")
    c2.metric("Optimized total cost", f"${optimized_tot['total_cost']:,.0f}",
              delta=f"${cost_delta:,.0f}", delta_color="inverse")
    c3.metric("Network avg CPP", f"${optimized_tot['network_avg_cpp']:.3f}",
              delta=f"{cpp_delta:+.3f}", delta_color="inverse")
    c4.metric("Savings vs. baseline", f"{savings_pct:.1f}%")

    # --- comparison table ---
    st.subheader("Market-level: Baseline vs. Optimized")
    merged = baseline_df.merge(
        optimized_df, on=["market_id", "market_name", "delivery_class", "volume"],
        suffixes=("_base", "_opt"),
    )
    merged["route_changed"] = merged["route_base"] != merged["route_opt"]
    merged["cpp_delta"] = (merged["total_cpp_opt"] - merged["total_cpp_base"]).round(4)
    merged["cost_delta"] = (merged["total_cost_opt"] - merged["total_cost_base"]).round(2)
    merged = merged.sort_values("cost_delta").reset_index(drop=True)

    view = merged[[
        "market_id", "market_name", "delivery_class", "volume",
        "route_base", "via_hub_base", "total_cpp_base", "total_cost_base",
        "route_opt", "via_hub_opt", "total_cpp_opt", "total_cost_opt",
        "route_changed", "cpp_delta", "cost_delta",
    ]]
    st.dataframe(view, use_container_width=True, hide_index=True)

    st.caption(f"Markets re-routed by optimizer: **{int(merged['route_changed'].sum())}** "
               f"of {len(merged)}")

    # --- detail tabs ---
    tab1, tab2 = st.tabs(["Baseline detail", "Optimized detail"])
    with tab1:
        st.dataframe(baseline_df, use_container_width=True, hide_index=True)
    with tab2:
        st.dataframe(optimized_df, use_container_width=True, hide_index=True)

    # --- downloads ---
    st.download_button("Download comparison (CSV)",
                       view.to_csv(index=False).encode(),
                       "baseline_vs_optimized.csv", "text/csv")


if run:
    model = build_network(ir_rate, oor_rate, mem_sort, dal_sort,
                          mem_active, dal_active, rate_per_mile, stem_rate, trailer_cap)
    render(model)
else:
    st.info("Set parameters and hub filters in the sidebar, then click "
            "**Run Baseline & Optimize**.")
