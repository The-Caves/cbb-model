"""College basketball v0.5 opponent-adjusted *recency-weight* research.

Uses the SAME joint opponent-adjusted score ridge as v0.4, with optional,
predeclared recency weights on current-season observations. No raw winning
streaks, unverified conference tiers, or future team profile snapshots.
2025-26 is retrospective score replay, not authenticated source availability.
Genuine new 2026-27 forecasts are frozen separately before tipoff.
"""
import argparse
import json
import math
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from cbb import ACTIVE_LIVE_VERSION, dt, eligible, fit, iso, predict, read_csv, utcnow
from team_directory import load_directory
from v05_transition_challenger import (
    final_game, frozen_v04, metrics, model_observation,
)

VERSION = "CBB_V05_OPP_ADJ_RECENCY_RESEARCH_20261113"
BASELINE = "v04"
LIGHTER = "lighter_carryover"
RECENCY_21 = "recency_21"
RECENCY_45 = "recency_45"
RECENCY_45_PRIOR15 = "recency_45_prior15"
VARIANTS = (RECENCY_21, RECENCY_45, RECENCY_45_PRIOR15)
ALL = (BASELINE, LIGHTER, *VARIANTS)
MIN_VALIDATE = 150
MIN_TEST = 150
MIN_FROZEN_PAIRS = 150

# Params fixed before looking at outcome comparisons. Normalization is a
# bounded decay with a nonzero floor, not an extra market or player feature.
CONFIGS = {
    BASELINE: {"prior_weight": 0.30, "alpha": 1.5},
    LIGHTER: {"prior_weight": 0.15, "alpha": 1.5},
    RECENCY_21: {"prior_weight": 0.30, "alpha": 1.5,
                 "recency_half_life_days": 21, "recency_floor": 0.60},
    RECENCY_45: {"prior_weight": 0.30, "alpha": 1.5,
                 "recency_half_life_days": 45, "recency_floor": 0.60},
    RECENCY_45_PRIOR15: {"prior_weight": 0.15, "alpha": 1.5,
                         "recency_half_life_days": 45, "recency_floor": 0.60},
}


def version(name):
    if name not in VARIANTS:
        raise ValueError("Unsupported recency challenger: " + str(name))
    return VERSION + "_" + name.upper()


def fit_variant(games, cutoff, year, d1, name, retrospective):
    if name not in CONFIGS:
        raise ValueError("Unknown variant")
    return fit(games, cutoff, test_season=year, verified_ids=d1,
               retrospective=retrospective, **CONFIGS[name])


def rated_strength(model, team):
    idx = model["idx"].get(str(team))
    if idx is None:
        return None
    n = len(model["idx"])
    return float(model["beta"][2 + idx] + model["beta"][2 + n + idx])


def matchup_strata(g, base_model, base_prediction, earlier):
    """Baseline-only subgroup assignments, identical across challengers.

    Schedule step-up is the largest prospective change in opposition
    difficulty relative to the known recent schedule. Ignore zero/one-game
    histories rather than asserting a dramatic step-up from no evidence.
    """
    h, a = str(g["home_id"]), str(g["away_id"])
    strengths = {t: rated_strength(base_model, t) for t in (h, a)}
    schedules = defaultdict(list)
    for prev in earlier:
        ph, pa = str(prev["home_id"]), str(prev["away_id"])
        if ph in (h,a):
            x = rated_strength(base_model, pa)
            if x is not None:
                schedules[ph].append(x)
        if pa in (h,a):
            x = rated_strength(base_model, ph)
            if x is not None:
                schedules[pa].append(x)
    gap = None
    if len(schedules[h]) >= 2 and len(schedules[a]) >= 2:
        gap = abs(sum(schedules[h])/len(schedules[h]) -
                  sum(schedules[a])/len(schedules[a]))
    stepup = False
    if len(schedules[h]) >= 2 and strengths[a] is not None:
        stepup = strengths[a] - sum(schedules[h])/len(schedules[h]) >= 8
    if len(schedules[a]) >= 2 and strengths[h] is not None:
        stepup = stepup or strengths[h] - sum(schedules[a])/len(schedules[a]) >= 8
    return {
        "early_min_under5": min(int(base_prediction["home_current_games"]),
                                int(base_prediction["away_current_games"])) < 5,
        "v04_favorite_12plus": abs(float(base_prediction["pred_margin"])) >= 12,
        "schedule_gap_6plus": gap is not None and gap >= 6,
        "opponent_stepup_8plus": bool(stepup),
        "schedule_gap_available": gap is not None,
    }


def backtest(games, verified, year=2026):
    """As-of reconstructed scores; never use later results in earlier fits."""
    d1 = {str(v) for v in verified}
    groups = defaultdict(list)
    for g in games:
        if (final_game(g, year, d1) and
                f"{year-1}-11-01" <= g["tip_utc"][:10] < f"{year}-03-01"):
            groups[g["tip_utc"][:10]].append(g)
    rows = {"november":defaultdict(list),
            "december_january_validation":defaultdict(list),
            "february_test":defaultdict(list)}
    strata = {"november":defaultdict(list),
              "december_january_validation":defaultdict(list),
              "february_test":defaultdict(list)}
    skipped = defaultdict(int)
    for day in sorted(groups):
        cutoff = dt(day + "T00:00:00Z")
        models = {}
        for name in ALL:
            try:
                m = fit_variant(games, cutoff, year, d1, name, True)
                if dt(m["training_latest_tip"]) >= cutoff:
                    raise AssertionError("Replay leaked later games")
                models[name] = m
            except ValueError:
                skipped["missing_asof_fitted_model"] += len(groups[day])
                models = {}
                break
        if not models:
            continue
        # Matched current-season schedule as of midnight UTC. This is the
        # identical verified D-I evidence cutoff used for the score model.
        previous = [g for g in games if int(g["season"]) == year
                    and str(g["home_id"]) in d1 and str(g["away_id"]) in d1
                    and eligible(g,cutoff,retrospective=True)]
        for g in groups[day]:
            predictions = {name:predict(model,g) for name,model in models.items()}
            if any(p is None for p in predictions.values()):
                skipped["missing_same_game_prediction"] += 1
                continue
            split = ("november" if day[:7] == f"{year-1}-11"
                     else "february_test" if day[:7] == f"{year}-02"
                     else "december_january_validation")
            tags = matchup_strata(g, models[BASELINE], predictions[BASELINE],
                                  previous)
            for name in ALL:
                obs = model_observation(g, predictions[name],
                                        models[name]["margin_sd"])
                rows[split][name].append(obs)
                for label, belongs in tags.items():
                    if belongs:
                        strata[split][label].append((str(g["game_id"]),name,obs))
    splits = {}
    for label in rows:
        comparison = {name:metrics(rows[label][name]) for name in ALL}
        if len({comparison[name]["n"] for name in ALL}) != 1:
            raise AssertionError("Unequal game IDs or denominators")
        subs = {}
        for key in ("early_min_under5","v04_favorite_12plus",
                    "schedule_gap_6plus","opponent_stepup_8plus"):
            group = strata[label][key]
            subs[key] = {name:metrics([obs for gid,other,obs in group
                                      if other==name]) for name in ALL}
            if len({subs[key][name]["n"] for name in ALL}) != 1:
                raise AssertionError("Incorrect per-variant subgroup selection")
        splits[label] = {"comparison":comparison,"strata":subs}
    validation = splits["december_january_validation"]["comparison"]
    february = splits["february_test"]["comparison"]
    enough_val = validation[BASELINE]["n"] >= MIN_VALIDATE
    enough_test = february[BASELINE]["n"] >= MIN_TEST
    # Predeclare combined error objective, with SU and Brier safeguards
    # against apparently better spreads achieved by poorer winner estimates.
    eligible_choices = [
        name for name in VARIANTS
        if enough_val and validation[name]["margin_mae"] <=
               validation[BASELINE]["margin_mae"] and
           validation[name]["su_accuracy"] >=
               validation[BASELINE]["su_accuracy"] - 0.005 and
           validation[name]["brier"] <= validation[BASELINE]["brier"] + 0.002
    ]
    chosen = min(eligible_choices,key=lambda name:(
        validation[name]["margin_mae"] +
        0.5*validation[name]["total_mae"],name)) if eligible_choices else None
    result = {
        "experiment":VERSION, "active_model":ACTIVE_LIVE_VERSION,
        "active_model_changed":False, "promoted":False,
        "historical_source_time_authenticated":False,
        "source":"Retrospectively archived ESPN final scores; cutoff-safe replay",
        "fixed_configs":CONFIGS, "selection_window":[f"{year-1}-12-01",f"{year}-01-31"],
        "diagnostic_november":[f"{year-1}-11-01",f"{year-1}-11-30"],
        "historical_heldout_window":[f"{year}-02-01",f"{year}-02-28"],
        "asof_rule":"Daily midnight UTC; exclude contemporaneous and future finals",
        "minimum_validation_games":MIN_VALIDATE,
        "minimum_test_games":MIN_TEST,
        "selected_on_validation_before_test":chosen,
        "selection_guard":"Margin MAE must improve vs v04, SU drop <= 0.5 percentage points, Brier increase <= 0.002",
        "selection_objective":"margin MAE + 0.5 * total MAE",
        "results":splits,
        "historical_test_sufficient":enough_test,
        "skipped":dict(skipped),
        "status":("RETROSPECTIVE_SCORE_REPLAY_ONLY" if enough_val and enough_test
                  else "INSUFFICIENT_MATCHED_GAME_COVERAGE"),
        "caveat":("February 2026 has been examined by earlier project experiments. "
                  "This is a repeated retrospective diagnostic, not an "
                  "independently untouched test or a proven betting edge. "
                  "Only new genuinely frozen pre-tip 2026-27 predictions can "
                  "supply independent prospective support."),
    }
    if chosen and enough_test:
        base, test = february[BASELINE], february[chosen]
        result["selected_february_delta"] = {
            "margin_mae_improvement_points":round(base["margin_mae"]-test["margin_mae"],3),
            "total_mae_improvement_points":round(base["total_mae"]-test["total_mae"],3),
            "su_accuracy_change_percentage_points":round(
                (test["su_accuracy"]-base["su_accuracy"])*100,2),
            "brier_improvement":round(base["brier"]-test["brier"],4),
        }
    return result


def validate_shadow(p, game, now):
    try:
        if p.get("model") not in [version(n) for n in VARIANTS]:
            return False
        name = p["variant"]
        if name not in VARIANTS or p["model"] != version(name):
            return False
        cutoff, tip = dt(p["captured_utc"]), dt(game["tip_utc"])
        if (not cutoff <= now or not cutoff < tip or
                (tip-cutoff).total_seconds()>72*3600 or
                dt(p["training_latest_tip"]) >= cutoff or
                p.get("tip_utc")!=game["tip_utc"] or
                str(p.get("game_id"))!=str(game["game_id"]) or
                str(p.get("home_id"))!=str(game["home_id"]) or
                str(p.get("away_id"))!=str(game["away_id"]) or
                p.get("champion_version")!=ACTIVE_LIVE_VERSION):
            return False
        if (abs(float(p["pred_home"])-float(p["pred_away"])-
                float(p["pred_margin"]))>.03 or
            abs(float(p["pred_home"])+float(p["pred_away"])-
                float(p["pred_total"]))>.03):
            return False
        return (p.get("prior_weight")==CONFIGS[name]["prior_weight"] and
                p.get("recency_half_life_days")==
                CONFIGS[name]["recency_half_life_days"] and
                p.get("recency_floor")==CONFIGS[name]["recency_floor"])
    except (KeyError,ValueError,TypeError,OverflowError):
        return False


def freeze(games, verified, now, ledger):
    year = now.year + int(now.month >= 7)
    d1 = {str(x) for x in verified}
    targets = [g for g in games if int(g["season"]) == year
               and str(g["home_id"]) in d1 and str(g["away_id"]) in d1
               and str(g.get("identity_conflict","0")) not in ("1","true","True")
               and str(g.get("score_conflict","0")) not in ("1","true","True")
               and now < dt(g["tip_utc"]) <= now+timedelta(hours=72)]
    if not targets:
        return {"status":"NO_FUTURE_D1_GAMES","captured":0}
    ledger=Path(ledger)
    existing=set()
    if ledger.exists():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            if line.strip():
                x=json.loads(line)
                existing.add((str(x["game_id"]), x["model"], x["captured_utc"]))
    records=[]
    for name in VARIANTS:
        try:
            fitted=fit_variant(games,now,year,d1,name,False)
            if dt(fitted["training_latest_tip"]) >= now:
                raise ValueError("Cannot freeze a model trained on future final")
        except ValueError:
            continue
        for g in targets:
            pred=predict(fitted,g)
            if pred is None:
                continue
            pred.update({
                "model":version(name), "variant":name,
                "captured_utc":iso(now), "champion_version":ACTIVE_LIVE_VERSION,
                "prior_weight":CONFIGS[name]["prior_weight"],
                "recency_half_life_days":CONFIGS[name]["recency_half_life_days"],
                "recency_floor":CONFIGS[name]["recency_floor"],
                "research_only":True,"market_edge_calibrated":False,
            })
            key=(str(g["game_id"]),pred["model"],pred["captured_utc"])
            if key not in existing and validate_shadow(pred,g,now):
                existing.add(key)
                records.append(pred)
    if records:
        ledger.parent.mkdir(parents=True,exist_ok=True)
        with ledger.open("a",encoding="utf-8") as stream:
            for item in records:
                stream.write(json.dumps(item,sort_keys=True)+"\n")
    return {"status":"GENUINE_PRETIP_RESEARCH_FREEZE",
            "captured":len(records),
            "games":len(set(x["game_id"] for x in records))}


def grade(games, verified, now, champion_ledger, shadow_ledger, year=None):
    year=year or now.year+int(now.month>=7)
    verified={str(x) for x in verified}
    baseline=frozen_v04(games,champion_ledger,now,verified,year)
    by_id={str(g["game_id"]):g for g in games}
    recent={}
    path=Path(shadow_ledger)
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec=json.loads(line)
            gid=str(rec.get("game_id"))
            g=by_id.get(gid)
            if (g is None or gid not in baseline or
                    not final_game(g,year,verified) or
                    not eligible(g,now,False) or
                    not validate_shadow(rec,g,now)):
                continue
            if abs((dt(rec["captured_utc"])-
                    dt(baseline[gid]["captured_utc"])).total_seconds())>7200:
                continue
            key=(gid,rec["model"])
            if key not in recent or recent[key]["captured_utc"]<rec["captured_utc"]:
                recent[key]=rec
    paired={}
    for name in VARIANTS:
        base_rows,challenger_rows=[],[]
        for (gid,model),rec in recent.items():
            if model!=version(name):
                continue
            g=by_id[gid]
            base_rows.append(model_observation(g,baseline[gid],12.0))
            challenger_rows.append(model_observation(g,rec,12.0))
        paired[name]={"v04":metrics(base_rows),"challenger":metrics(challenger_rows)}
        if paired[name]["v04"]["n"]!=paired[name]["challenger"]["n"]:
            raise AssertionError("Unequal genuine pre-tip comparison groups")
    return {"status":"GENUINE_PRETIP_MATCHED_RESEARCH",
            "graded_by_variant":paired,
            "minimum_completed_pairs_for_review":MIN_FROZEN_PAIRS,
            "active_v04_unchanged":True,
            "promotion_gate":"NOT_AUTO_PROMOTED",
            "warning":("All candidates use distinct immutable versions, and "
                       "only exactly matched verified games with captures "
                       "within two hours are compared. At least 150 frozen "
                       "pairs across independent periods are required for review.")}


def run(root="data", now=None, historical=False):
    root=Path(root)
    now=now or utcnow()
    try:
        verified=load_directory(root/"team_directory.json")
    except (ValueError,OSError):
        verified={}
    games=read_csv(root/"games.csv")
    if historical:
        report=backtest(games,verified)
        target=root/"reports"/"recency_challenger_2026.json"
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n",
                          encoding="utf-8")
        return {"status":report["status"],"selection":report["selected_on_validation_before_test"],
                "february":report["results"]["february_test"]["comparison"],
                "november":report["results"]["november"]["comparison"]}
    freezing=freeze(games,verified,now,root/"recency_shadow_ledger.jsonl")
    results=grade(games,verified,now,root/"forecast_ledger.jsonl",
                  root/"recency_shadow_ledger.jsonl")
    target=root/"reports"/"recency_prospective_2027.json"
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps({
        "experiment":VERSION, "as_of_utc":iso(now),
        "active_v04_unchanged":True, "freeze":freezing,"grade":results,
    },indent=2,sort_keys=True)+"\n",encoding="utf-8")
    return {"freeze":freezing,"grade":results}


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--root",default="data")
    parser.add_argument("--historical",action="store_true")
    args=parser.parse_args()
    print(json.dumps(run(args.root,historical=args.historical),indent=2))
