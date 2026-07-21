# Hub Cost-to-Serve Model — Usage Guide

## Streamlit app (interactive UI)

`streamlit_app.py` puts every parameter — hubs, lanes, markets, last-mile
rates, hub-proximity multipliers — into editable tables in a browser UI, on
top of the same model in `hub_cost_to_serve_model.py`.

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

Three tabs:
- **Network Setup** — edit hubs (incl. active/inactive), lanes, markets,
  last-mile rates, and hub-proximity overrides directly in data tables. Add
  or delete rows freely; validation errors surface at the bottom before you
  move on.
- **Cost to Serve** — current-state (as-routed) report and optimized-routing
  report, each with total cost, network avg CPP, and a cost-burden chart.
- **Hub Toggle** — pick any hub, flip it active/inactive, and see the
  market-level route/cost delta table plus system-level cost delta metrics.

Both entry points share `hub_cost_to_serve_model.py`, so anything you verify
against the CLI script (`current_state_cost_to_serve.csv`, etc.) will match
what the Streamlit app shows for the same inputs.

---


`hub_cost_to_serve_model.py` prices every parcel's **full arc** — linehaul in,
sort touch at any hub, last-mile out — into one CPP and total cost-to-serve
figure per market, then lets you toggle a hub on/off and see the system-wide
cost delta. Run it directly (`python3 hub_cost_to_serve_model.py`) to see a
worked example, or import the classes into your own script/notebook.

## Swap in your real network

1. **`HubNetworkModel(last_mile_rate_table=...)`** — set your base $/parcel
   last-mile cost by delivery classification (IR/OOR or whatever segments
   you use). This is an input, not the organizing structure — everything
   downstream is framed around total CPP.

2. **`model.add_hub(Hub(node_id, name, sort_cost_per_parcel, active))`** —
   one entry per existing or candidate hub. `active=False` for a hub you
   haven't built yet, or one you're evaluating closing.

3. **`model.add_lane(Lane(from_node, to_node, distance_miles, rate_per_mile,
   trailer_capacity, min_trailers))`** — every transportation link: origin→hub,
   hub→hub, hub→market, and any direct origin→market bypass lanes. Linehaul
   cost is volume-sensitive (trailer fill), so shared trunk lanes get cheaper
   per parcel as more markets consolidate onto them — this is where hub
   investments earn their keep beyond the sort fee.

4. **`model.add_market(Market(market_id, name, origin_node, volume,
   delivery_class, current_path, last_mile_multiplier,
   last_mile_multiplier_by_hub))`** — one per market cluster. `current_path`
   is your real, as-routed path today (for the "current state" baseline).
   `last_mile_multiplier_by_hub` lets a hub's *location* — not just its
   existence — change last-mile cost (a closer hub shortens the final stem).

## What to run

- `model.report(current_assignment)` — as-routed CPP breakdown and total
  cost burden per market, ranked highest to lowest.
- `model.optimize_assignment()` + `model.report(...)` — best achievable
  routing given whichever hubs are currently active.
- `model.toggle_hub(hub_id, active=True/False)` — the bonus feature: flips
  one hub, re-solves routing, and returns baseline vs. scenario reports, a
  market-by-market delta table (which markets re-route and by how much),
  and a system-level cost delta.

## Notes on the routing solve

Route selection uses an iterative best-response heuristic: each market picks
its cheapest available route given current lane volumes, lane volumes get
re-aggregated, and it repeats until stable. This is what lets consolidation
economics show up (a market shifting onto a hub lane changes that lane's
CPP for every other market already on it) without needing a full LP solver.
For very large networks (100+ markets, deep hop counts) you'd want to swap
this for a proper min-cost-flow solver, but the cost functions and reporting
layer carry over unchanged.
