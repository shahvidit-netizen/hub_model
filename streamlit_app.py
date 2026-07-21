"""
streamlit_app.py

Interactive Streamlit front-end for hub_cost_to_serve_model.py.

Run with:
    pip install streamlit pandas
    streamlit run streamlit_app.py

Every network parameter is editable in the UI:
  - Hubs (sort cost, active/inactive)
  - Lanes (distance, rate, trailer capacity, min trailers)
  - Markets (volume, delivery class, current path, last-mile multipliers)
  - Last-mile base rate table (by delivery class)
  - Per-hub last-mile multiplier overrides (hub proximity effect)

Three tabs:
  1. Network Setup   - edit every input table
  2. Cost to Serve   - current-state vs. optimized-routing reports, cost
                        burden ranking, charts
  3. Hub Toggle      - flip one hub on/off, see the market-level and
                        system-level cost delta
"""

import pandas as pd
import streamlit as st

from hub_cost_to_serve_model import Hub, Lane, Market, HubNetworkModel


# --------------------------------------------------------------------------
# Demo defaults (same network as the standalone script) — used to seed
# session_state the first time the app loads, and via "Reset to demo data".
# --------------------------------------------------------------------------

def demo_tables():
    hubs_df = pd.DataFrame([
        {"node_id": "HUB_MEM", "name": "Memphis Regional Hub", "sort_cost_per_parcel": 0.35, "active": True},
        {"node_id": "HUB_DAL", "name": "Dallas Candidate Hub", "sort_cost_per_parcel": 0.32, "active": False},
    ])

    lanes_df = pd.DataFrame([
        {"from_node": "FC_ATL", "to_node": "HUB_MEM", "distance_miles": 390, "rate_per_mile": 2.60, "trailer_capacity": 220, "min_trailers": 1},
        {"from_node": "FC_ATL", "to_node": "HUB_DAL", "distance_miles": 450, "rate_per_mile": 2.60, "trailer_capacity": 220, "min_trailers": 1},
        {"from_node": "HUB_MEM", "to_node": "HUB_DAL", "distance_miles": 430, "rate_per_mile": 2.55, "trailer_capacity": 220, "min_trailers": 1},
        {"from_node": "FC_ATL", "to_node": "MKT_SHV", "distance_miles": 355, "rate_per_mile": 2.60, "trailer_capacity": 220, "min_trailers": 1},
        {"from_node": "FC_ATL", "to_node": "MKT_TUL", "distance_miles": 470, "rate_per_mile": 2.60, "trailer_capacity": 220, "min_trailers": 1},
        {"from_node": "FC_ATL", "to_node": "MKT_OKC", "distance_miles": 590, "rate_per_mile": 2.60, "trailer_capacity": 220, "min_trailers": 1},
        {"from_node": "FC_ATL", "to_node": "MKT_LIT", "distance_miles": 340, "rate_per_mile": 2.60, "trailer_capacity": 220, "min_trailers": 1},
        {"from_node": "FC_ATL", "to_node": "MKT_FTW", "distance_miles": 730, "rate_per_mile": 2.60, "trailer_capacity": 220, "min_trailers": 1},
        {"from_node": "FC_ATL", "to_node": "MKT_ABI", "distance_miles": 850, "rate_per_mile": 2.60, "trailer_capacity": 220, "min_trailers": 1},
        {"from_node": "HUB_MEM", "to_node": "MKT_SHV", "distance_miles": 300, "rate_per_mile": 2.20, "trailer_capacity": 180, "min_trailers": 1},
        {"from_node": "HUB_MEM", "to_node": "MKT_TUL", "distance_miles": 360, "rate_per_mile": 2.20, "trailer_capacity": 180, "min_trailers": 1},
        {"from_node": "HUB_MEM", "to_node": "MKT_LIT", "distance_miles": 140, "rate_per_mile": 2.20, "trailer_capacity": 180, "min_trailers": 1},
        {"from_node": "HUB_MEM", "to_node": "MKT_OKC", "distance_miles": 470, "rate_per_mile": 2.20, "trailer_capacity": 180, "min_trailers": 1},
        {"from_node": "HUB_DAL", "to_node": "MKT_SHV", "distance_miles": 190, "rate_per_mile": 2.20, "trailer_capacity": 180, "min_trailers": 1},
        {"from_node": "HUB_DAL", "to_node": "MKT_TUL", "distance_miles": 260, "rate_per_mile": 2.20, "trailer_capacity": 180, "min_trailers": 1},
        {"from_node": "HUB_DAL", "to_node": "MKT_OKC", "distance_miles": 205, "rate_per_mile": 2.20, "trailer_capacity": 180, "min_trailers": 1},
        {"from_node": "HUB_DAL", "to_node": "MKT_FTW", "distance_miles": 35, "rate_per_mile": 2.20, "trailer_capacity": 180, "min_trailers": 1},
        {"from_node": "HUB_DAL", "to_node": "MKT_ABI", "distance_miles": 155, "rate_per_mile": 2.20, "trailer_capacity": 180, "min_trailers": 1},
        {"from_node": "HUB_DAL", "to_node": "MKT_LIT", "distance_miles": 330, "rate_per_mile": 2.20, "trailer_capacity": 180, "min_trailers": 1},
    ])

    markets_df = pd.DataFrame([
        {"market_id": "MKT_SHV", "name": "Shreveport Cluster", "origin_node": "FC_ATL", "volume": 1400, "delivery_class": "OOR", "current_path": "FC_ATL,MKT_SHV", "last_mile_multiplier": 1.05},
        {"market_id": "MKT_TUL", "name": "Tulsa Cluster", "origin_node": "FC_ATL", "volume": 1150, "delivery_class": "OOR", "current_path": "FC_ATL,HUB_MEM,MKT_TUL", "last_mile_multiplier": 1.10},
        {"market_id": "MKT_OKC", "name": "Oklahoma City Cluster", "origin_node": "FC_ATL", "volume": 2200, "delivery_class": "IR", "current_path": "FC_ATL,MKT_OKC", "last_mile_multiplier": 1.00},
        {"market_id": "MKT_LIT", "name": "Little Rock Cluster", "origin_node": "FC_ATL", "volume": 1900, "delivery_class": "IR", "current_path": "FC_ATL,HUB_MEM,MKT_LIT", "last_mile_multiplier": 0.95},
        {"market_id": "MKT_FTW", "name": "Fort Worth Cluster", "origin_node": "FC_ATL", "volume": 2600, "delivery_class": "IR", "current_path": "FC_ATL,MKT_FTW", "last_mile_multiplier": 1.00},
        {"market_id": "MKT_ABI", "name": "Abilene Cluster", "origin_node": "FC_ATL", "volume": 800, "delivery_class": "OOR", "current_path": "FC_ATL,MKT_ABI", "last_mile_multiplier": 1.15},
    ])

    last_mile_df = pd.DataFrame([
        {"delivery_class": "IR", "base_rate": 2.10},
        {"delivery_class": "OOR", "base_rate": 3.85},
    ])

    multiplier_df = pd.DataFrame([
        {"market_id": "MKT_SHV", "hub_id": "HUB_DAL", "multiplier": 0.90},
        {"market_id": "MKT_SHV", "hub_id": "HUB_MEM", "multiplier": 0.98},
        {"market_id": "MKT_TUL", "hub_id": "HUB_DAL", "multiplier": 0.95},
        {"market_id": "MKT_TUL", "hub_id": "HUB_MEM", "multiplier": 1.02},
        {"market_id": "MKT_OKC", "hub_id": "HUB_DAL", "multiplier": 0.92},
        {"market_id": "MKT_OKC", "hub_id": "HUB_MEM", "multiplier": 1.00},
        {"market_id": "MKT_LIT", "hub_id": "HUB_MEM", "multiplier": 0.90},
        {"market_id": "MKT_LIT", "hub_id": "HUB_DAL", "multiplier": 1.05},
        {"market_id": "MKT_FTW", "hub_id": "HUB_DAL", "multiplier": 0.80},
        {"market_id": "MKT_FTW", "hub_id": "HUB_MEM", "multiplier": 1.05},
        {"market_id": "MKT_ABI", "hub_id": "HUB_DAL", "multiplier": 0.88},
        {"market_id": "MKT_ABI", "hub_id": "HUB_MEM", "multiplier": 1.10},
    ])

    return hubs_df, lanes_df, markets_df, last_mile_df, multiplier_df


def init_state():
    if "hubs_df" not in st.session_state:
        hubs_df, lanes_df, markets_df, last_mile_df, multiplier_df = demo_tables()
        st.session_state.hubs_df = hubs_df
        st.session_state.lanes_df = lanes_df
        st.session_state.markets_df = markets_df
        st.session_state.last_mile_df = last_mile_df
        st.session_state.multiplier_df = multiplier_df


def reset_to_demo():
    hubs_df, lanes_df, markets_df, last_mile_df, multiplier_df = demo_tables()
    st.session_state.hubs_df = hubs_df
    st.session_state.lanes_df = lanes_df
    st.session_state.markets_df = markets_df
    st.session_state.last_mile_df = last_mile_df
    st.session_state.multiplier_df = multiplier_df


# --------------------------------------------------------------------------
# Build a HubNetworkModel from the current editable tables
# --------------------------------------------------------------------------

def build_model_from_state() -> HubNetworkModel:
    last_mile_rate_table = {
        row["delivery_class"]: float(row["base_rate"])
        for _, row in st.session_state.last_mile_df.iterrows()
        if str(row["delivery_class"]).strip()
    }
    model = HubNetworkModel(last_mile_rate_table=last_mile_rate_table)

    for _, row in st.session_state.hubs_df.iterrows():
        if not str(row["node_id"]).strip():
            continue
        model.add_hub(Hub(
            node_id=str(row["node_id"]).strip(),
            name=str(row["name"]),
            sort_cost_per_parcel=float(row["sort_cost_per_parcel"]),
            active=bool(row["active"]),
        ))

    for _, row in st.session_state.lanes_df.iterrows():
        if not str(row["from_node"]).strip() or not str(row["to_node"]).strip():
            continue
        model.add_lane(Lane(
            from_node=str(row["from_node"]).strip(),
            to_node=str(row["to_node"]).strip(),
            distance_miles=float(row["distance_miles"]),
            rate_per_mile=float(row["rate_per_mile"]),
            trailer_capacity=int(row["trailer_capacity"]),
            min_trailers=int(row["min_trailers"]),
        ))

    # per-market hub multiplier overrides
    mult_lookup = {}
    for _, row in st.session_state.multiplier_df.iterrows():
        mid, hid = str(row.get("market_id", "")).strip(), str(row.get("hub_id", "")).strip()
        if not mid or not hid:
            continue
        mult_lookup.setdefault(mid, {})[hid] = float(row["multiplier"])

    for _, row in st.session_state.markets_df.iterrows():
        if not str(row["market_id"]).strip():
            continue
        path_str = str(row["current_path"])
        path = [p.strip() for p in path_str.split(",") if p.strip()]
        mid = str(row["market_id"]).strip()
        model.add_market(Market(
            market_id=mid,
            name=str(row["name"]),
            origin_node=str(row["origin_node"]).strip(),
            volume=int(row["volume"]),
            delivery_class=str(row["delivery_class"]).strip(),
            current_path=path,
            last_mile_multiplier=float(row["last_mile_multiplier"]),
            last_mile_multiplier_by_hub=mult_lookup.get(mid, {}),
        ))

    return model


def validate_model(model: HubNetworkModel) -> list:
    """Return a list of human-readable problems, if any, so the UI can warn
    before trying to route (instead of crashing on a bad edit)."""
    problems = []
    if not model.last_mile_rate_table:
        problems.append("No last-mile rate table rows defined.")
    for mid, market in model.markets.items():
        if market.delivery_class not in model.last_mile_rate_table:
            problems.append(f"Market {mid}: delivery_class '{market.delivery_class}' has no matching last-mile rate row.")
        if len(market.current_path) < 2:
            problems.append(f"Market {mid}: current_path must have at least 2 nodes (origin,...,market_id).")
        elif market.current_path[-1] != mid:
            problems.append(f"Market {mid}: current_path must end with the market_id itself ('{mid}').")
        elif market.current_path[0] != market.origin_node:
            problems.append(f"Market {mid}: current_path must start with origin_node ('{market.origin_node}').")
        routes = model.enumerate_routes(market.origin_node, mid, max_hops=4)
        if not routes:
            problems.append(f"Market {mid}: no feasible route exists at all between '{market.origin_node}' and '{mid}' given current lanes.")
    return problems


# --------------------------------------------------------------------------
# Streamlit page
# --------------------------------------------------------------------------

st.set_page_config(page_title="Hub Cost-to-Serve Model", layout="wide")
init_state()

st.title("Hub Cost-to-Serve Model")
st.caption(
    "Total end-to-end CPP (linehaul + sort touch + last-mile) by market, "
    "and the system-wide cost delta from adding, removing, or repositioning a hub."
)

with st.sidebar:
    st.header("Controls")
    if st.button("Reset all tables to demo data", use_container_width=True):
        reset_to_demo()
        st.rerun()
    st.markdown("---")
    max_hops = st.slider("Max intermediate hops to simulate per route", 1, 5, 3)
    iterations = st.slider("Routing solve iterations", 1, 20, 8)
    st.markdown("---")
    st.caption(
        "Edit hubs, lanes, and markets in the **Network Setup** tab. "
        "Every table supports adding/deleting rows."
    )

tab_setup, tab_cost, tab_toggle = st.tabs(["Network Setup", "Cost to Serve", "Hub Toggle"])

# ---- Tab 1: Network Setup --------------------------------------------------
with tab_setup:
    st.subheader("Hubs")
    st.caption("node_id must be unique. Toggle 'active' to simulate a hub not existing (yet).")
    st.session_state.hubs_df = st.data_editor(
        st.session_state.hubs_df, num_rows="dynamic", use_container_width=True, key="hubs_editor",
        column_config={
            "sort_cost_per_parcel": st.column_config.NumberColumn(format="$%.2f"),
            "active": st.column_config.CheckboxColumn(),
        },
    )

    st.subheader("Lanes")
    st.caption(
        "Every transportation link: origin→hub, hub→hub, hub→market, or a direct origin→market bypass. "
        "Linehaul cost is priced per trailer load and divided by volume actually on the lane, "
        "so shared/consolidated lanes get cheaper per parcel as volume grows."
    )
    st.session_state.lanes_df = st.data_editor(
        st.session_state.lanes_df, num_rows="dynamic", use_container_width=True, key="lanes_editor",
        column_config={
            "rate_per_mile": st.column_config.NumberColumn(format="$%.2f"),
        },
    )

    st.subheader("Markets")
    st.caption(
        "current_path is a comma-separated node list from origin_node to market_id, "
        "e.g. FC_ATL,HUB_MEM,MKT_TUL — this is how the market is routed TODAY."
    )
    st.session_state.markets_df = st.data_editor(
        st.session_state.markets_df, num_rows="dynamic", use_container_width=True, key="markets_editor",
    )

    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("Last-mile base rate table")
        st.caption("Base $/parcel last-mile cost by delivery classification (input, not the analysis frame).")
        st.session_state.last_mile_df = st.data_editor(
            st.session_state.last_mile_df, num_rows="dynamic", use_container_width=True, key="lastmile_editor",
            column_config={"base_rate": st.column_config.NumberColumn(format="$%.2f")},
        )
    with col_b:
        st.subheader("Hub proximity last-mile multipliers (optional)")
        st.caption(
            "Override last_mile_multiplier for a market when a specific hub is the last "
            "node before it — models a hub being physically closer/farther from a cluster."
        )
        st.session_state.multiplier_df = st.data_editor(
            st.session_state.multiplier_df, num_rows="dynamic", use_container_width=True, key="mult_editor",
        )

    st.markdown("---")
    model_preview = build_model_from_state()
    problems = validate_model(model_preview)
    if problems:
        st.error("Fix the following before results will be reliable:\n\n" + "\n".join(f"- {p}" for p in problems))
    else:
        st.success("Network configuration is valid.")


# ---- shared model build for the other two tabs -----------------------------
model = build_model_from_state()
problems = validate_model(model)


def cost_burden_chart(df: pd.DataFrame, value_col: str, title: str):
    chart_df = df.set_index("market_name")[[value_col]]
    st.bar_chart(chart_df, use_container_width=True)


# ---- Tab 2: Cost to Serve ---------------------------------------------------
with tab_cost:
    if problems:
        st.warning("Resolve the issues in Network Setup to see results.")
    else:
        current_assignment = {
            mid: m.current_path for mid, m in model.markets.items() if model.route_is_active(m.current_path)
        }
        missing = set(model.markets) - set(current_assignment)
        if missing:
            st.warning(
                f"These markets' current_path is not usable with the active hub set and were "
                f"excluded from the 'as-routed' report (they still appear in Optimized): {sorted(missing)}"
            )

        st.subheader("Current state — as routed today")
        if current_assignment:
            current_df = model.report(current_assignment)
            totals = model.system_totals(current_df)
            c1, c2, c3 = st.columns(3)
            c1.metric("Total volume", f"{totals['total_volume']:,}")
            c2.metric("Total cost to serve", f"${totals['total_cost']:,.2f}")
            c3.metric("Network avg CPP", f"${totals['network_avg_cpp']:.4f}")
            st.dataframe(current_df, use_container_width=True, hide_index=True)
            st.caption("Highest cost-burden markets (by total $ cost-to-serve)")
            cost_burden_chart(current_df, "total_cost", "Total cost-to-serve by market")

        st.subheader("Optimized routing — best routes given today's active hub set")
        optimized_assignment = model.optimize_assignment(max_hops=max_hops, iterations=iterations)
        optimized_df = model.report(optimized_assignment)
        opt_totals = model.system_totals(optimized_df)
        c1, c2, c3 = st.columns(3)
        c1.metric("Total volume", f"{opt_totals['total_volume']:,}")
        c2.metric("Total cost to serve", f"${opt_totals['total_cost']:,.2f}")
        c3.metric("Network avg CPP", f"${opt_totals['network_avg_cpp']:.4f}")
        st.dataframe(optimized_df, use_container_width=True, hide_index=True)
        cost_burden_chart(optimized_df, "total_cpp", "CPP by market (optimized)")


# ---- Tab 3: Hub Toggle ------------------------------------------------------
with tab_toggle:
    if problems:
        st.warning("Resolve the issues in Network Setup to run a toggle scenario.")
    elif not model.hubs:
        st.info("Add at least one hub in Network Setup to use this tab.")
    else:
        hub_ids = list(model.hubs.keys())
        col1, col2 = st.columns([2, 1])
        with col1:
            selected_hub = st.selectbox(
                "Hub to toggle",
                hub_ids,
                format_func=lambda h: f"{h} — {model.hubs[h].name} (currently {'ACTIVE' if model.hubs[h].active else 'inactive'})",
            )
        with col2:
            new_state = st.radio("New state", ["Active", "Inactive"], horizontal=True)
        new_active = new_state == "Active"

        if st.button("Run toggle scenario", type="primary"):
            result = model.toggle_hub(selected_hub, active=new_active, max_hops=max_hops, iterations=iterations)
            sd = result["system_delta"]

            st.subheader(f"System-level impact of setting {selected_hub} to {new_state.upper()}")
            c1, c2, c3 = st.columns(3)
            c1.metric(
                "Total cost to serve",
                f"${sd['total_cost_scenario']:,.2f}",
                delta=f"${sd['total_cost_delta']:,.2f}",
                delta_color="inverse",
            )
            c2.metric(
                "Network avg CPP",
                f"${sd['network_avg_cpp_scenario']:.4f}",
                delta=f"${sd['network_avg_cpp_scenario'] - sd['network_avg_cpp_baseline']:.4f}",
                delta_color="inverse",
            )
            c3.metric("Markets re-routed", sd["markets_rerouted"])

            st.subheader("Market-level delta")
            delta_df = result["market_delta"][[
                "market_id", "market_name", "volume", "route_baseline", "route_scenario",
                "route_changed", "cpp_delta", "cost_delta",
            ]]
            st.dataframe(
                delta_df.style.background_gradient(subset=["cost_delta"], cmap="RdYlGn_r"),
                use_container_width=True, hide_index=True,
            )

            st.caption("Cost delta by market (negative = cheaper under the new hub state)")
            chart_df = delta_df.set_index("market_name")[["cost_delta"]]
            st.bar_chart(chart_df, use_container_width=True)

            with st.expander("Full baseline report"):
                st.dataframe(result["baseline_report"], use_container_width=True, hide_index=True)
            with st.expander("Full scenario report"):
                st.dataframe(result["scenario_report"], use_container_width=True, hide_index=True)
