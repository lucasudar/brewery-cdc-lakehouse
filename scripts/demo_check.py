"""Demo assertions, run inside the api container (stdlib only, no host dependencies).

python demo_check.py baseline            -> prints the pre-change state as JSON
python demo_check.py verify '<baseline>'  -> polls the API until every change is visible
"""

import json
import sys
import time
import urllib.error
import urllib.request

API = "http://127.0.0.1:8000"
TIMEOUT_S = 60


def get(path):
    try:
        with urllib.request.urlopen(API + path, timeout=5) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, None


def saison():
    _, body = get("/v1/beers?style=Saison&limit=1000")
    beers = [
        b for b in body["data"] if b["name"] == "CDC Test Saison"
    ]
    _, gold = get(
        "/v1/metrics/daily-style-consumption?style=Saison&limit=1000"
    )
    litres = round(sum(r["litres_consumed"] for r in gold["data"]), 3)
    return beers, litres


def baseline():
    beers, litres = saison()
    return {"saison_beers": len(beers), "saison_gold_litres": litres}


def checks(base):
    beers, litres = saison()
    newest = max((b["beer_id"] for b in beers), default=None)
    _, consumption = (
        get(f"/v1/consumption?beer_id={newest}")
        if newest
        else (None, {"meta": {"total": 0}})
    )
    _, beer1 = get("/v1/beers/1")
    _, c10 = get("/v1/consumption/10")
    c20_status, _ = get("/v1/consumption/20")
    return {
        "INSERT beer 'CDC Test Saison'        (silver.beer_factory)": len(
            beers
        )
        == base["saison_beers"] + 1,
        "INSERT its consumption, 0.5 l        (silver.novisto_consumption)": consumption[
            "meta"
        ][
            "total"
        ]
        >= 1,
        "UPDATE beer 1 abv -> 9.9             (silver.beer_factory)": beer1[
            "data"
        ][
            "abv"
        ]
        == 9.9,
        "UPDATE consumption 10 -> 2.0 l       (silver.novisto_consumption)": c10[
            "data"
        ][
            "quantity_l"
        ]
        == 2.0,
        "DELETE consumption 20 -> 404         (silver.novisto_consumption)": c20_status
        == 404,
        # simulate_changes.sql inserts 0.5 l for *every* beer named 'CDC Test Saison', so re-runs add more.
        "Saison litres up in today's mart     (gold.daily_style_consumption)": round(
            litres - base["saison_gold_litres"], 3
        )
        == 0.5 * (base["saison_beers"] + 1),
    }


def verify(base):
    started = time.monotonic()
    seen = {}
    while True:
        elapsed = time.monotonic() - started
        for name, ok in checks(base).items():
            if ok and name not in seen:
                seen[name] = elapsed
                print(
                    f"  ✔ {name}  visible after {elapsed:5.1f}s",
                    flush=True,
                )
        if len(seen) == len(checks(base)):
            break
        if elapsed > TIMEOUT_S:
            missing = [n for n in checks(base) if n not in seen]
            print(
                f"  ✘ not visible after {TIMEOUT_S}s: {missing}",
                flush=True,
            )
            sys.exit(1)
        time.sleep(0.5)
    _, fresh = get("/freshness")
    print("\nFreshness (source commit -> Iceberg snapshot):")
    for t in fresh["tables"]:
        extra = (
            f"  up_to_date={t['up_to_date']}"
            if t["up_to_date"] is not None
            else ""
        )
        print(
            f"  {t['table']:<32} {t['source_to_table_latency_s']:>6}s{extra}"
        )
    print(
        f"\nEnd to end: every change visible through the API after {max(seen.values()):.1f}s (target: 60s)"
    )


if __name__ == "__main__":
    if sys.argv[1] == "baseline":
        print(json.dumps(baseline()))
    else:
        verify(json.loads(sys.argv[2]))
