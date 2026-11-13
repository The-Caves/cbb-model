"""Dated team-strength trajectory challenger (research only).

Compare each team's as-of rating to its own 21-day-old as-of rating, plus
opponent-adjusted last-five form and schedule strength. Ridge corrections
are learned only from 2025 November-December replays, ablated on January,
and checked on February. Those 2025-26 game outcomes were archived later;
historical results are NOT authenticated historical pre-tip source evidence.

A separate append-only 2026-27 ledger freezes genuine research forecasts.
The active v0.4 model and its frozen forecasts are never modified.
"""
import argparse
import json
import math
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import numpy as np
from scipy.stats import norm

from cbb import ACTIVE_LIVE_VERSION, dt, eligible, fit, iso, predict, read_csv, utcnow
from team_directory import load_directory
from v05_transition_challenger import frozen_v04

VERSION = "CBB_PROFILE_TRAJECTORY_V05_RESEARCH"
BASELINE = ACTIVE_LIVE_VERSION
LOOKBACK_DAYS = 21
RIDGE_ALPHA = 180.0
MIN_TRAIN = 180
MIN_VALIDATION = 100
MIN_HOLDOUT = 100
CAP_MARGIN, CAP_TOTAL = 6.0, 8.0
FEATURE_NAMES = (
    "rating_21d_change_gap", "offensive_21d_change_sum",
    "defensive_21d_change_sum", "recent_form_vs_rating_gap",
    "schedule_strength_gap", "current_sample_ratio",
)
FEATURE_SETS = {
    "trajectory_only": (0, 1, 2, 5),
    "trajectory_context": (0, 1, 2, 3, 4, 5),
}


def strength(model, team):
    tid = str(team)
    idx = model["idx"].get(tid)
    if idx is None:
        return None
    n = len(model["idx"])
    b = model["beta"]
    offense = float(b[2+idx])
    defense = float(b[2+n+idx])
    return {"offense": offense, "defense": defense,
            "strength": offense + defense}


def fit_asof(games, cutoff, year, verified, retrospective):
    return fit(games, cutoff, test_season=year, verified_ids=verified,
               retrospective=retrospective, prior_weight=0.30, alpha=1.5)


def make_profiles(games, verified, cutoff, year, retrospective, latest=None, older=None):
    """All profile values depend only on known pre-cutoff finals.

    Later seasons, excluded opponents, score disputes, and current match
    outcomes never enter the snapshot. Old and current score models are both
    fitted as of their own explicit cutoffs.
    """
    latest = latest or fit_asof(games, cutoff, year, verified, retrospective)
    older_cutoff = cutoff - timedelta(days=LOOKBACK_DAYS)
    older = older or fit_asof(games, older_cutoff, year, verified, retrospective)
    if (dt(latest["cutoff"]) != cutoff or
            dt(older["cutoff"]) != older_cutoff or
            dt(latest["training_latest_tip"]) >= cutoff or
            dt(older["training_latest_tip"]) >= older_cutoff):
        raise ValueError("Profile scores were not fit at their declared as-of cutoffs")
    observed = defaultdict(list)
    valid = {str(x) for x in verified}
    for g in games:
        if (int(g["season"]) != year or str(g["home_id"]) not in valid or
                str(g["away_id"]) not in valid or
                not eligible(g, cutoff, retrospective)):
            continue
        h, a = str(g["home_id"]), str(g["away_id"])
        margin = float(g["home_score"])-float(g["away_score"])
        neutral = str(g.get("neutral", "0")) in ("1", "True", "true")
        venue = 0 if neutral else float(latest["home_court_points"])
        for tid, opp, team_margin, sign in ((h,a,margin,1), (a,h,-margin,-1)):
            opponent = strength(latest, opp)
            if opponent is None:
                continue
            # Average opponent rating is based on *today's* prior-to-tip
            # knowledge, not opponents' future final-season ratings.
            adjusted_margin = team_margin + opponent["strength"] - sign*venue
            observed[tid].append((
                g["tip_utc"], str(g["game_id"]), opponent["strength"],
                adjusted_margin,
            ))
    profiles = {}
    for team in valid:
        current, prior = strength(latest, team), strength(older, team)
        if current is None or prior is None:
            continue
        vals = sorted(observed.get(team, []), key=lambda v: (v[0], v[1]))
        last = vals[-5:]
        n = len(vals)
        profiles[team] = {
            "team_id": team,
            "as_of_utc": iso(cutoff),
            "old_as_of_utc": iso(older_cutoff),
            "current_d1_games": n,
            "current_offense": current["offense"],
            "current_defense": current["defense"],
            "current_strength": current["strength"],
            "old_offense": prior["offense"],
            "old_defense": prior["defense"],
            "old_strength": prior["strength"],
            "average_opponent_strength": (
                sum(v[2] for v in vals)/n if n else None),
            "recent_adjusted_margin": (
                sum(v[3] for v in last)/len(last) if last else None),
            "historical_source_capture_authenticated": not retrospective,
        }
    return latest, older, profiles


def matchup_features(home, away):
    """Reject missing/asymmetric old team profiles rather than inventing zero."""
    if (not home or not away or
            min(home["current_d1_games"], away["current_d1_games"]) < 2):
        return None
    necessary = ("average_opponent_strength", "recent_adjusted_margin")
    if any(home.get(x) is None or away.get(x) is None for x in necessary):
        return None
    # Current adjusted baseline is trained on the same pregame information.
    # Recent form measures departure from that as-of baseline.
    r_h = home["recent_adjusted_margin"] - home["current_strength"]
    r_a = away["recent_adjusted_margin"] - away["current_strength"]
    vector = [
        (home["current_strength"]-home["old_strength"]) -
        (away["current_strength"]-away["old_strength"]),
        (home["current_offense"]-home["old_offense"]) +
        (away["current_offense"]-away["old_offense"]),
        (home["current_defense"]-home["old_defense"]) +
        (away["current_defense"]-away["old_defense"]),
        r_h-r_a,
        home["average_opponent_strength"] - away["average_opponent_strength"],
        min(1.0, min(home["current_d1_games"], away["current_d1_games"])/10.0),
    ]
    if not all(math.isfinite(x) for x in vector):
        return None
    return np.asarray(vector, dtype=float)


def ridge(rows, indices, outcome, alpha=RIDGE_ALPHA):
    if len(rows) < MIN_TRAIN:
        return None
    x = np.asarray([row["features"][list(indices)] for row in rows])
    y = np.asarray([row[outcome] for row in rows])
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale < 1e-8] = 1.0
    z = (x-mean)/scale
    design = np.column_stack([np.ones(len(z)), z])
    pen = np.eye(design.shape[1])*alpha
    pen[0,0] = 0
    beta = np.linalg.solve(design.T@design + pen, design.T@y)
    return {"indices": list(indices), "means": mean.tolist(),
            "scales": scale.tolist(), "weights": beta.tolist(),
            "n_train": len(rows)}


def train(rows, indices):
    return {
        "margin": ridge(rows, indices, "margin_residual"),
        "total": ridge(rows, indices, "total_residual"),
    }


def correction(row, trained, outcome):
    model = trained.get(outcome)
    if model is None:
        return 0.0
    values = row["features"][model["indices"]]
    centered = (values-np.asarray(model["means"]))/np.asarray(model["scales"])
    b = np.asarray(model["weights"])
    result = float(b[0] + b[1:]@centered)
    return max(-CAP_MARGIN if outcome=="margin" else -CAP_TOTAL,
               min(CAP_MARGIN if outcome=="margin" else CAP_TOTAL, result))


def candidate(row, trained):
    return {
        "margin": row["base_margin"]+correction(row,trained,"margin"),
        "total": row["base_total"]+correction(row,trained,"total"),
    }


def champion(row):
    return {"margin": row["base_margin"], "total": row["base_total"]}


def score(rows, fn):
    if not rows:
        return {"n": 0, "su_accuracy": None, "margin_mae": None,
                "total_mae": None, "margin_bias": None,
                "total_bias": None, "brier": None}
    margin_err, total_err, correct, probs = [], [], [], []
    for row in rows:
        pred = fn(row)
        m, t = pred["margin"], pred["total"]
        actual_m, actual_t = row["actual_margin"], row["actual_total"]
        margin_err.append(m-actual_m)
        total_err.append(t-actual_t)
        correct.append(int((m>0)==(actual_m>0)))
        p = float(norm.cdf(m/max(8.0,row["base_margin_sd"])))
        probs.append((p-int(actual_m>0))**2)
    n=len(rows)
    return {
        "n": n, "su_accuracy": round(sum(correct)/n, 4),
        "margin_mae": round(sum(abs(x) for x in margin_err)/n, 3),
        "total_mae": round(sum(abs(x) for x in total_err)/n, 3),
        "margin_bias": round(sum(margin_err)/n, 3),
        "total_bias": round(sum(total_err)/n, 3),
        "brier": round(sum(probs)/n, 4),
    }


def replay(games, verified, year=2026):
    """Score-based as-of reconstruction: no fake historical roster timestamps."""
    groups=defaultdict(list)
    for g in games:
        if (int(g["season"])==year and
                str(g["home_id"]) in verified and str(g["away_id"]) in verified and
                str(g.get("completed")) in ("1","True","true") and
                str(g.get("identity_conflict","0")) not in ("1","True","true") and
                str(g.get("score_conflict","0")) not in ("1","True","true") and
                g["home_score"] not in ("",None) and g["away_score"] not in ("",None)
                and f"{year-1}-11-01" <= g["tip_utc"][:10] < f"{year}-03-01"):
            groups[g["tip_utc"][:10]].append(g)
    rows=[]
    skipped=defaultdict(int)
    for day in sorted(groups):
        cutoff=dt(day+"T00:00:00Z")
        try:
            latest, old, profiles=make_profiles(
                games, verified, cutoff, year, retrospective=True)
        except ValueError:
            skipped["missing_historical_rating"]+=len(groups[day])
            continue
        for g in groups[day]:
            base=predict(latest,g)
            if base is None:
                skipped["missing_v04_prediction"]+=1
                continue
            h=profiles.get(str(g["home_id"]))
            a=profiles.get(str(g["away_id"]))
            f=matchup_features(h,a)
            if f is None:
                skipped["missing_asof_profile_features"]+=1
                continue
            actual_m=float(g["home_score"])-float(g["away_score"])
            actual_t=float(g["home_score"])+float(g["away_score"])
            rows.append({
                "game_id":str(g["game_id"]), "day":day,
                "features":f,
                "base_margin":float(base["pred_margin"]),
                "base_total":float(base["pred_total"]),
                "base_margin_sd":float(latest["margin_sd"]),
                "actual_margin":actual_m, "actual_total":actual_t,
                "margin_residual":actual_m-float(base["pred_margin"]),
                "total_residual":actual_t-float(base["pred_total"]),
                "min_current_games":min(h["current_d1_games"],a["current_d1_games"]),
                "sos_gap":abs(float(f[4])),
            })
    return rows, {"day_snapshots_reconstructed":len(groups),
                  "replay_qualified_games":len(rows),
                  "skipped":dict(skipped)}


def run_backtest(root="data", year=2026, games=None, verified=None):
    root=Path(root)
    games=games if games is not None else read_csv(root/"games.csv")
    verified=set(verified if verified is not None
                 else load_directory(root/"team_directory.json"))
    rows, coverage=replay(games,verified,year)
    train_rows=[r for r in rows if r["day"]<f"{year}-01-01"]
    validate=[r for r in rows if f"{year}-01-01"<=r["day"]<f"{year}-02-01"]
    test=[r for r in rows if f"{year}-02-01"<=r["day"]<f"{year}-03-01"]
    result={
        "model":VERSION, "baseline":BASELINE,
        "status":"INSUFFICIENT_DATA",
        "active_model_changed":False, "promoted":False,
        "historical_source_timestamps_authenticated":False,
        "asof_lookback_days":LOOKBACK_DAYS,
        "train_dates":[f"{year-1}-11-01",f"{year-1}-12-31"],
        "validation_dates":[f"{year}-01-01",f"{year}-01-31"],
        "heldout_dates":[f"{year}-02-01",f"{year}-02-28"],
        "features":list(FEATURE_NAMES), "ridge_alpha":RIDGE_ALPHA,
        "correction_caps_points":{"margin":CAP_MARGIN,"total":CAP_TOTAL},
        "sample_requirements":{"training":MIN_TRAIN,"validation":MIN_VALIDATION,"heldout":MIN_HOLDOUT},
        "counts":{"train":len(train_rows),"validation":len(validate),"heldout":len(test)},
        "coverage":coverage,
        "selection":None,
        "validation":{},"heldout":{},
        "warning":("Reconstructed older v0.4 team ratings use correct game-date "
                   "cutoffs but ESPN historical final score archives were acquired "
                   "retrospectively. Not authenticated historical pre-tip features, "
                   "not sportsbook ATS/OU or an independently prospective result."),
    }
    if len(train_rows)<MIN_TRAIN:
        result["status"]="INSUFFICIENT_TRAINING_DATA"
        return result
    trained={name:train(train_rows,indices)
             for name,indices in FEATURE_SETS.items()}
    result["validation"]["v04"]=score(validate,champion)
    if len(validate)<MIN_VALIDATION:
        result["status"]="INSUFFICIENT_VALIDATION_DATA"
        return result
    for name in FEATURE_SETS:
        result["validation"][name]=score(validate,
            lambda r,t=trained[name]:candidate(r,t))
    chosen=min(FEATURE_SETS,key=lambda name:(
        result["validation"][name]["margin_mae"]+
        0.5*result["validation"][name]["total_mae"],name))
    result["selection"]=chosen
    # Store only the November/December-fitted coefficients, never a fit on
    # February results. Daily shadow forecasts can use them unchanged.
    result["frozen_training_coefficients"]=trained[chosen]
    if len(test)<MIN_HOLDOUT:
        result["status"]="INSUFFICIENT_HELDOUT_DATA"
        return result
    result["heldout"]={
        "v04":score(test,champion),
        chosen:score(test,lambda r:candidate(r,trained[chosen])),
    }
    result["heldout_strata"]={}
    # Define strata by frozen champion and pregame profiles for same games.
    for name, subset in (
        ("early_less_than_6", [r for r in test if r["min_current_games"]<6]),
        ("v04_large_favorite_12plus",[r for r in test if abs(r["base_margin"])>=12]),
        ("sos_gap_6plus",[r for r in test if r["sos_gap"]>=6]),
    ):
        result["heldout_strata"][name]={
            "v04":score(subset,champion),
            chosen:score(subset,lambda r:candidate(r,trained[chosen])),
        }
    result["status"]="RETROSPECTIVE_ASOF_REPLAY_COMPLETE"
    result["heldout_improvement"]={
        "margin_mae_points":round(result["heldout"]["v04"]["margin_mae"]-
                                  result["heldout"][chosen]["margin_mae"],3),
        "total_mae_points":round(result["heldout"]["v04"]["total_mae"]-
                                 result["heldout"][chosen]["total_mae"],3),
        "su_percentage_points":round(100*(
            result["heldout"][chosen]["su_accuracy"]-
            result["heldout"]["v04"]["su_accuracy"]),2),
    }
    result["needs_real_frozen_2026_27_holdout"]=True
    return result


def freeze(games, verified, now, selected_report, ledger):
    """Store genuine pre-tip predictions, never reconstructed as live after the game."""
    if (selected_report.get("status")!="RETROSPECTIVE_ASOF_REPLAY_COMPLETE" or
            not selected_report.get("frozen_training_coefficients")):
        return {"status":"BLOCKED_NO_VALIDATED_HISTORICAL_TRAINING","captured":0}
    year=now.year+int(now.month>=7)
    targets=[g for g in games if
             int(g["season"])==year and
             str(g["home_id"]) in verified and str(g["away_id"]) in verified and
             str(g.get("identity_conflict","0")) not in ("1","True","true") and
             str(g.get("score_conflict","0")) not in ("1","True","true") and
             now<dt(g["tip_utc"])<=now+timedelta(hours=72)]
    if not targets:
        return {"status":"NO_UPCOMING_D1_GAMES","captured":0}
    try:
        latest, old, profiles=make_profiles(
            games,verified,now,year,retrospective=False)
    except ValueError:
        return {"status":"PASS_NO_ASOF_TRAINING","captured":0}
    past=set()
    ledger=Path(ledger)
    if ledger.exists():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            if line.strip():
                obj=json.loads(line)
                past.add((str(obj.get("game_id")),obj.get("captured_utc"),obj.get("model")))
    done=[]
    for g in targets:
        h,a=profiles.get(str(g["home_id"])),profiles.get(str(g["away_id"]))
        f=matchup_features(h,a)
        base=predict(latest,g)
        if f is None or base is None:
            continue
        row={"features":f,"base_margin":float(base["pred_margin"]),
             "base_total":float(base["pred_total"])}
        outcome=candidate(row,selected_report["frozen_training_coefficients"])
        ph=(outcome["total"]+outcome["margin"])/2
        pa=(outcome["total"]-outcome["margin"])/2
        rec={
            "game_id":str(g["game_id"]),
            "home_id":str(g["home_id"]),"away_id":str(g["away_id"]),
            "tip_utc":g["tip_utc"],"captured_utc":iso(now),
            "old_profile_as_of_utc":h["old_as_of_utc"],
            "new_profile_as_of_utc":h["as_of_utc"],
            "training_latest_tip":latest["training_latest_tip"],
            "old_training_latest_tip":old["training_latest_tip"],
            "model":VERSION,"champion_version":BASELINE,
            "research_only":True,"source_timing_authenticated":True,
            "selected_feature_set":selected_report["selection"],
            "pred_home":round(ph,2),"pred_away":round(pa,2),
            "pred_margin":round(ph-pa,2),"pred_total":round(ph+pa,2),
            "base_margin":float(base["pred_margin"]),
            "base_total":float(base["pred_total"]),
        }
        key=rec["game_id"],rec["captured_utc"],rec["model"]
        if key in past or dt(rec["training_latest_tip"])>=now:
            continue
        done.append(rec)
        past.add(key)
    if done:
        ledger.parent.mkdir(parents=True,exist_ok=True)
        with ledger.open("a",encoding="utf-8") as fd:
            for record in done:
                fd.write(json.dumps(record,sort_keys=True)+"\n")
    return {"status":"FROZEN_PRETIP_RESEARCH_ONLY","captured":len(done),
            "eligible_upcoming_games":len(targets),
            "qualified_with_dated_profiles":len(done)}


def grade(games, verified, now, research_ledger, base_ledger, year=None):
    """Like-for-like legitimate pre-tip comparison within 2h capture window."""
    year=year or now.year+int(now.month>=7)
    by_id={str(g["game_id"]):g for g in games}
    base=frozen_v04(games,base_ledger,now,verified,year)
    rows=[]
    path=Path(research_ledger)
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            p=json.loads(line)
            g=by_id.get(str(p.get("game_id")))
            if (g is None or g["game_id"] not in base or
                    p.get("model")!=VERSION or
                    not eligible(g,now,False)):
                continue
            try:
                if (p.get("tip_utc")!=g["tip_utc"] or
                    p.get("home_id")!=str(g["home_id"]) or
                    p.get("away_id")!=str(g["away_id"]) or
                    p.get("champion_version",BASELINE)!=BASELINE or
                    dt(p["captured_utc"])>=dt(g["tip_utc"]) or
                    dt(p["new_profile_as_of_utc"])!=dt(p["captured_utc"]) or
                    dt(p["old_profile_as_of_utc"])!=(
                        dt(p["captured_utc"])-timedelta(days=LOOKBACK_DAYS)) or
                    dt(p["training_latest_tip"])>=dt(p["captured_utc"]) or
                    dt(p["old_training_latest_tip"])>=dt(p["old_profile_as_of_utc"]) or
                    abs((float(p["pred_home"])-float(p["pred_away"]))-
                        float(p["pred_margin"]))>0.05 or
                    abs((float(p["pred_home"])+float(p["pred_away"]))-
                        float(p["pred_total"]))>0.05 or
                    abs((dt(p["captured_utc"])-
                         dt(base[g["game_id"]]["captured_utc"])).total_seconds())>7200):
                    continue
                actual_m=float(g["home_score"])-float(g["away_score"])
                actual_t=float(g["home_score"])+float(g["away_score"])
                b=base[g["game_id"]]
                # Freeze vs frozen on precisely the same game and score.
                rows.append({
                    "game_id":str(g["game_id"]),"capture":p["captured_utc"],
                    "actual_margin":actual_m,"actual_total":actual_t,
                    "v04_margin":float(b["pred_margin"]),
                    "v04_total":float(b["pred_total"]),
                    "v05_margin":float(p["pred_margin"]),
                    "v05_total":float(p["pred_total"]),
                })
            except (KeyError,TypeError,ValueError):
                continue
    best={}
    for row in rows:
        if row["game_id"] not in best or best[row["game_id"]]["capture"]<row["capture"]:
            best[row["game_id"]]=row
    observations=list(best.values())
    def outcome(kind):
        if not observations:
            return {"n":0,"su_accuracy":None,"margin_mae":None,"total_mae":None}
        n=len(observations)
        return {
            "n":n,
            "su_accuracy":round(sum(
                (r[f"{kind}_margin"]>0)==(r["actual_margin"]>0)
                for r in observations)/n,4),
            "margin_mae":round(sum(
                abs(r[f"{kind}_margin"]-r["actual_margin"])
                for r in observations)/n,3),
            "total_mae":round(sum(
                abs(r[f"{kind}_total"]-r["actual_total"])
                for r in observations)/n,3),
        }
    return {
        "status":"TRUE_FROZEN_PRETIP_PAIRED_RESEARCH",
        "paired_games":len(observations),
        "minimum_for_independent_review":150,
        "promotion_gate":("READY_FOR_INDEPENDENT_REVIEW" if len(observations)>=150
                          else "INSUFFICIENT_PAIRED_GAMES"),
        "v04":outcome("v04"),"v05_trajectory":outcome("v05"),
        "active_v04_unchanged":True,
        "note":"Identical final outcomes; both predictions must be frozen before tip within two hours.",
    }


def run(root="data", now=None, historical=False):
    root=Path(root)
    now=now or utcnow()
    try:
        verified=set(load_directory(root/"team_directory.json"))
    except (ValueError,OSError):
        verified=set()
    games=read_csv(root/"games.csv")
    target=root/"reports"/"profile_trajectory_backtest_2026.json"
    if historical:
        report=run_backtest(root=root,year=2026,games=games,verified=verified)
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n",
                          encoding="utf-8")
        return {"status":report["status"],"selected":report["selection"],
                "counts":report["counts"],"heldout":report.get("heldout")}
    if not target.exists():
        return {"status":"BLOCKED_NO_HISTORICAL_MODEL"}
    report=json.loads(target.read_text(encoding="utf-8"))
    frozen=freeze(games,verified,now,report,root/"profile_trajectory_shadow_ledger.jsonl")
    graded=grade(games,verified,now,
                 root/"profile_trajectory_shadow_ledger.jsonl",
                 root/"forecast_ledger.jsonl")
    out=root/"reports"/"profile_trajectory_prospective.json"
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps({"model":VERSION,"as_of_utc":iso(now),
                               "freeze":frozen,"grade":graded,
                               "live_v04_unchanged":True},indent=2,sort_keys=True)+"\n",
                   encoding="utf-8")
    return {"freeze":frozen,"grade":graded}


if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",default="data")
    ap.add_argument("--historical",action="store_true")
    args=ap.parse_args()
    print(json.dumps(run(args.root,historical=args.historical),indent=2))
