"""v0.5 matchup + Four Factors research built on lighter prior-season carryover.

Game score prediction and matchup data are jointly opponent-adjusted, strictly
as of the cutoff. Research forecasts are immutable and frozen before tip.
Historical sources were fetched retrospectively, not authentic 2025 book odds.
"""
import argparse
import json
import math
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import numpy as np

from cbb import ACTIVE_LIVE_VERSION, dt, eligible, fit, iso, predict, read_csv, utcnow
from boxscore_features import load_accepted
from factor_challenger import features as factors_features, FEATURE_NAMES as FACTOR_NAMES
from market_capture import last_pretip_quotes, validate_quote, utc
from team_directory import load_directory
from team_intelligence import build as build_intelligence
from v05_transition_challenger import final_game, frozen_v04, metrics, model_observation

MODEL_PREFIX = "CBB_V05_LIGHT_PRIOR_MATCHUP_20261113"
VARIANTS = ("core", "interaction")
VERSION_BY_VARIANT = {k:MODEL_PREFIX+"_"+k.upper() for k in VARIANTS}
SOURCE_VERSION = ACTIVE_LIVE_VERSION
PRIOR_WEIGHT = 0.15
ALPHA = 1.5
RIDGE_ALPHA = 250.0
MARGIN_CAP, TOTAL_CAP = 5.0, 7.0
MIN_TRAIN, MIN_VALIDATION, MIN_HOLDOUT = 200, 100, 100
MIN_PAIRED_PROSPECTIVE = 150
FEATURES = tuple(FACTOR_NAMES) + (
    "estimated_tempo", "tempo_clash",
    "efficiency_offense_defense_gap", "efficiency_total",
    "threes_shot_quality_interaction", "threes_variance_proxy",
    "turnover_press_interaction", "offensive_rebound_interaction",
    "free_throw_pressure_interaction",
)
# Compact preregistered matchup and fuller nonlinear feature families.
CORE_COLUMNS = tuple(range(len(FACTOR_NAMES)))
ALL_COLUMNS = tuple(range(len(FEATURES)))
COLUMNS = {"core":CORE_COLUMNS, "interaction":ALL_COLUMNS}


def as_float(profile, field):
    v=profile.get(field)
    if v is None:
        raise ValueError("missing pre-tip player/team source feature")
    v=float(v)
    if not math.isfinite(v):
        raise ValueError("nonfinite pre-tip factor")
    return v


def matchup_features(home, away):
    """Observed current-season offense-versus-opponent allowed interactions.

    All rate features originate from as-of team_intelligence.build, which
    already checks verified D-I opponent identities, actual final capture
    time, and excludes any incomplete/mismatched team game box scores.
    """
    core = factors_features(home,away)
    if core is None:
        return None
    try:
        h,a=home,away
        def v(t,name,suffix): return as_float(t,name+"_"+suffix+"_adj")
        hp=v(h,"pace_est","for"); hd=v(h,"pace_est","allowed")
        ap=v(a,"pace_est","for"); ad=v(a,"pace_est","allowed")
        ho=v(h,"off_rtg","for"); hao=v(h,"off_rtg","allowed")
        ao=v(a,"off_rtg","for"); aao=v(a,"off_rtg","allowed")
        h3=v(h,"three_rate","for"); a3=v(a,"three_rate","for")
        hefg=v(h,"efg_pct","for"); aefg=v(a,"efg_pct","for")
        hefgallow=v(h,"efg_pct","allowed"); aefgallow=v(a,"efg_pct","allowed")
        htov=v(h,"tov_pct","for"); atov=v(a,"tov_pct","for")
        htovallow=v(h,"tov_pct","allowed"); atovallow=v(a,"tov_pct","allowed")
        horb=v(h,"orb_pct","for"); aorb=v(a,"orb_pct","for")
        horballow=v(h,"orb_pct","allowed"); aorballow=v(a,"orb_pct","allowed")
        hf=v(h,"ft_rate","for"); af=v(a,"ft_rate","for")
        hfall=v(h,"ft_rate","allowed"); afall=v(a,"ft_rate","allowed")
        # Derived features include interactions (not just more raw columns).
        projected_tempo=(hp+hd+ap+ad)/4.0
        efficiency_home=ho-aao
        efficiency_away=ao-hao
        three_shot=(h3*(hefg-aefgallow) - a3*(aefg-hefgallow))*100
        variance=(h3+a3)*((hefg-aefgallow)**2+(aefg-hefgallow)**2)*100
        turnovers=(htov*atovallow-atov*htovallow)*100
        rebounding=(horb*aorballow-aorb*horballow)*100
        foul_pressure=(hf*afall-af*hfall)*100
        additions=[
            projected_tempo,
            abs(hp-ap),
            efficiency_home-efficiency_away,
            efficiency_home+efficiency_away,
            three_shot,variance,turnovers,rebounding,foul_pressure,
        ]
        out=np.concatenate((core,np.asarray(additions,dtype=float)))
        if len(out)!=len(FEATURES) or not np.isfinite(out).all():
            return None
        return out
    except (ValueError,TypeError,KeyError,OverflowError):
        return None


def score_fit(games,cutoff,year,verified,retrospective):
    return fit(games,cutoff,test_season=year,verified_ids=verified,
               retrospective=retrospective,alpha=ALPHA,prior_weight=PRIOR_WEIGHT)


def asof_profiles(games,accepted,verified,cutoff,year,retrospective):
    model=score_fit(games,cutoff,year,verified,retrospective)
    if dt(model["training_latest_tip"])>=cutoff:
        raise AssertionError("model fitted with current/future tip")
    intelligence=build_intelligence(
        games,accepted,verified,cutoff,year=year,
        retrospective=retrospective,score_model=model)
    if intelligence.get("as_of_utc")!=iso(cutoff):
        raise AssertionError("profile cutoff mismatch")
    return model,intelligence


def examples(games,accepted,verified,year=2026):
    verified={str(v) for v in verified}
    groups=defaultdict(list)
    for g in games:
        if (final_game(g,year,verified) and
                f"{year-1}-11-01"<=g["tip_utc"][:10]<f"{year}-03-01"):
            groups[g["tip_utc"][:10]].append(g)
    rows=[]
    skipped=defaultdict(int)
    for day in sorted(groups):
        cutoff=dt(day+"T00:00:00Z")
        try:
            fitted,profiles=asof_profiles(games,accepted,verified,cutoff,year,True)
            champion=fit(games,cutoff,test_season=year,
                         verified_ids=verified,retrospective=True,
                         alpha=ALPHA,prior_weight=0.30)
            if dt(champion["training_latest_tip"])>=cutoff:
                raise AssertionError("v04 comparator uses later results")
        except ValueError:
            skipped["missing_dated_score_fit"]+=len(groups[day])
            continue
        if profiles.get("status")!="RETROSPECTIVE_RESEARCH_ONLY":
            skipped["missing_dated_adjusted_profiles"]+=len(groups[day])
            continue
        for g in groups[day]:
            p=predict(fitted,g)
            v04=predict(champion,g)
            home=profiles["teams"].get(str(g["home_id"]))
            away=profiles["teams"].get(str(g["away_id"]))
            feat=matchup_features(home,away) if home and away else None
            if p is None or v04 is None or feat is None:
                skipped["missing_pretip_matchup_features"]+=1
                continue
            actual_margin=float(g["home_score"])-float(g["away_score"])
            actual_total=float(g["home_score"])+float(g["away_score"])
            rows.append({
                "game_id":str(g["game_id"]),"day":day,
                "features":feat,"actual_margin":actual_margin,
                "actual_total":actual_total,
                "base_margin":float(p["pred_margin"]),
                "base_total":float(p["pred_total"]),
                "v04_margin":float(v04["pred_margin"]),
                "v04_total":float(v04["pred_total"]),
                "margin_residual":actual_margin-float(p["pred_margin"]),
                "total_residual":actual_total-float(p["pred_total"]),
                "base_margin_sd":float(fitted["margin_sd"]),
                "min_current_games":min(int(home["current_d1_games"]),
                                        int(away["current_d1_games"])),
                "min_box_games":min(int(home["boxscore_games_current"]),
                                    int(away["boxscore_games_current"])),
                "schedule_gap":abs(float(feat[0])),
            })
    return rows,{"matched_features":len(rows),
                 "eligible_d1_finals":sum(len(x) for x in groups.values()),
                 "skipped":dict(skipped),"historical_sources_contemporaneous":False}


def ridge(rows,columns,outcome):
    if len(rows)<MIN_TRAIN:
        return None
    x=np.asarray([r["features"][list(columns)] for r in rows],dtype=float)
    y=np.asarray([r[outcome] for r in rows],dtype=float)
    mu=x.mean(axis=0)
    scale=x.std(axis=0)
    scale[scale<1e-8]=1.0
    z=(x-mu)/scale
    design=np.column_stack([np.ones(len(rows)),z])
    penalty=np.eye(design.shape[1])*RIDGE_ALPHA
    penalty[0,0]=0
    coef=np.linalg.solve(design.T@design+penalty,design.T@y)
    return {
        "columns":list(columns),"mean":mu.tolist(),"scale":scale.tolist(),
        "coef":coef.tolist(),"n":len(rows),
        "trained_period":"2025-11-01_to_2025-12-31",
    }


def train(rows,variant):
    cols=COLUMNS[variant]
    return {"margin":ridge(rows,cols,"margin_residual"),
            "total":ridge(rows,cols,"total_residual")}


def adjusted(row,trained):
    def correction(label,limit):
        model=trained[label]
        if model is None:
            return 0
        f=np.asarray(row["features"])[model["columns"]]
        center=(f-np.asarray(model["mean"]))/np.asarray(model["scale"])
        coef=np.asarray(model["coef"])
        d=float(coef[0]+coef[1:]@center)
        return float(np.clip(d,-limit,limit))
    return {"margin":row["base_margin"]+correction("margin",MARGIN_CAP),
            "total":row["base_total"]+correction("total",TOTAL_CAP)}


def plain(row):
    return {"margin":row["base_margin"],"total":row["base_total"]}


def v04(row):
    return {"margin":row["v04_margin"],"total":row["v04_total"]}


def scores(rows,predictor):
    if not rows:
        return {"n":0,"margin_mae":None,"total_mae":None,
                "su_accuracy":None,"brier":None,"margin_bias":None,"total_bias":None}
    from scipy.stats import norm
    mm,tt,su,bb=[],[],[],[]
    for r in rows:
        p=predictor(r)
        m,t=float(p["margin"]),float(p["total"])
        mm.append(m-r["actual_margin"])
        tt.append(t-r["actual_total"])
        su.append(int((m>0)==(r["actual_margin"]>0)))
        prob=float(norm.cdf(m/max(8,float(r["base_margin_sd"]))))
        bb.append((prob-int(r["actual_margin"]>0))**2)
    n=len(rows)
    return {"n":n,"margin_mae":round(sum(abs(x) for x in mm)/n,3),
            "total_mae":round(sum(abs(x) for x in tt)/n,3),
            "su_accuracy":round(sum(su)/n,4),
            "brier":round(sum(bb)/n,4),
            "margin_bias":round(sum(mm)/n,3),
            "total_bias":round(sum(tt)/n,3)}


def retrospective(games,accepted,verified,year=2026):
    rows,coverage=examples(games,accepted,verified,year)
    train_rows=[r for r in rows if r["day"]<f"{year}-01-01"]
    validation=[r for r in rows if f"{year}-01-01"<=r["day"]<f"{year}-02-01"]
    holdout=[r for r in rows if f"{year}-02-01"<=r["day"]<f"{year}-03-01"]
    report={
        "status":"INSUFFICIENT_DATA","model_family":MODEL_PREFIX,
        "active_version":SOURCE_VERSION,"live_version_unchanged":True,
        "base_prior_weight":PRIOR_WEIGHT,
        "candidate_versions":VERSION_BY_VARIANT,
        "feature_names":list(FEATURES),"feature_columns":{
            v:list(cols) for v,cols in COLUMNS.items()},
        "ridge_alpha":RIDGE_ALPHA,"correction_caps_points":{
            "margin":MARGIN_CAP,"total":TOTAL_CAP},
        "train_dates":["2025-11-01","2025-12-31"],
        "validation_dates":["2026-01-01","2026-01-31"],
        "historical_holdout_dates":["2026-02-01","2026-02-28"],
        "counts":{"training":len(train_rows),"validation":len(validation),
                  "historical_holdout":len(holdout)},
        "coverage":coverage,"validation":{},"february":{},
        "selected_on_january":None,
        "retrospective_source_publication_authenticated":False,
        "betting_lines_historically_authenticated":False,
        "previous_february_period_already_examined":True,
        "promotion_gate":"NEEDS_INDEPENDENT_PROSPECTIVE_EVIDENCE",
        "warning":("No ATS/O-U claims from retrospective games without "
                   "authenticated pre-tip book lines. Previously reviewed February "
                   "outcomes are descriptive, not fresh independent holdout."),
    }
    if len(train_rows)<MIN_TRAIN:
        report["status"]="INSUFFICIENT_TRAIN"
        return report
    trained={name:train(train_rows,name) for name in VARIANTS}
    report["frozen_historical_coefficients"]=trained
    for name in ("v04","lighter",*VARIANTS):
        fn=(v04 if name=="v04" else plain if name=="lighter"
            else lambda r,m=name:adjusted(r,trained[m]))
        report["validation"][name]=scores(validation,fn)
    report["january_lighter"]=report["validation"]["lighter"]
    if len(validation)<MIN_VALIDATION:
        report["status"]="INSUFFICIENT_VALIDATION"
        return report
    # Pre-registered gating. A January model must reduce at least one
    # projection error, not simply increase straight-up percent versus
    # unadjusted lighter carryover on the same games.
    compare=report["validation"]["lighter"]
    qualified=[name for name in VARIANTS
               if (report["validation"][name]["margin_mae"]<=compare["margin_mae"]-0.03
                   or report["validation"][name]["total_mae"]<=compare["total_mae"]-0.08)
               and report["validation"][name]["su_accuracy"]>=compare["su_accuracy"]-.005
               and report["validation"][name]["brier"]<=compare["brier"]+.0015]
    chosen=min(qualified,key=lambda n:(
        report["validation"][n]["margin_mae"]+
        .5*report["validation"][n]["total_mae"],n)) if qualified else None
    report["selected_on_january"]=chosen
    if len(holdout)<MIN_HOLDOUT:
        report["status"]="INSUFFICIENT_FEBRUARY"
        return report
    for name in ("v04","lighter",*VARIANTS):
        fn=(v04 if name=="v04" else plain if name=="lighter"
            else lambda r,m=name:adjusted(r,trained[m]))
        report["february"][name]=scores(holdout,fn)
    strata={
        "champion_large_favorite_12plus":[r for r in holdout if abs(r["base_margin"])>=12],
        "strength_schedule_gap_6plus":[r for r in holdout if r["schedule_gap"]>=6],
        "boxscore_sample_under5":[r for r in holdout if r["min_box_games"]<5],
        "near_pickem_under6":[r for r in holdout if abs(r["base_margin"])<6],
        "conference_season_february":[r for r in holdout],
    }
    report["february_strata"]={
        label:{name:scores(rs,v04 if name=="v04" else plain if name=="lighter"
                           else lambda r,m=name:adjusted(r,trained[m]))
               for name in ("v04","lighter",*VARIANTS)}
        for label,rs in strata.items()}
    report["status"]="HISTORICAL_RESEARCH_COMPLETE"
    if chosen:
        b,p=report["february"]["lighter"],report["february"][chosen]
        report["selected_february_delta_vs_lighter"]={
            "margin_mae_improvement_points":round(b["margin_mae"]-p["margin_mae"],3),
            "total_mae_improvement_points":round(b["total_mae"]-p["total_mae"],3),
            "su_accuracy_change_pp":round(100*(p["su_accuracy"]-b["su_accuracy"]),2),
            "brier_improvement":round(b["brier"]-p["brier"],4),
        }
    return report


def validate_frozen(p,g,now):
    try:
        variant=p["variant"]
        if variant not in VARIANTS or p["model"]!=VERSION_BY_VARIANT[variant]:
            return False
        capture,tip=dt(p["captured_utc"]),dt(g["tip_utc"])
        if (not capture<=now or not capture<tip or
                tip-capture>timedelta(hours=72) or
                p["tip_utc"]!=g["tip_utc"] or
                str(p["game_id"])!=str(g["game_id"]) or
                str(p["home_id"])!=str(g["home_id"]) or
                str(p["away_id"])!=str(g["away_id"]) or
                dt(p["training_latest_tip"])>=capture or
                p["champion_version"]!=SOURCE_VERSION or
                p["base_prior_weight"]!=PRIOR_WEIGHT):
            return False
        return (abs(float(p["pred_home"])-float(p["pred_away"])-
                    float(p["pred_margin"]))<.035 and
                abs(float(p["pred_home"])+float(p["pred_away"])-
                    float(p["pred_total"]))<.035)
    except (KeyError,ValueError,TypeError,OverflowError):
        return False


def freeze(games,accepted,verified,now,report,ledger):
    year=now.year+int(now.month>=7)
    future=[g for g in games if int(g["season"])==year
            and str(g["home_id"]) in verified and str(g["away_id"]) in verified
            and str(g.get("identity_conflict","0")) not in ("1","true","True")
            and str(g.get("score_conflict","0")) not in ("1","true","True")
            and now<dt(g["tip_utc"])<=now+timedelta(hours=72)]
    if not future:
        return {"status":"NO_UPCOMING_VERIFIED_D1_GAMES","captured":0}
    if (report.get("status")!="HISTORICAL_RESEARCH_COMPLETE" or
            set(report.get("frozen_historical_coefficients",{}))!=set(VARIANTS)):
        return {"status":"INSUFFICIENT_HISTORICAL_COEFFICIENTS","captured":0}
    try:
        base,profiles=asof_profiles(games,accepted,verified,now,year,False)
    except ValueError:
        return {"status":"NO_SOURCE_OBSERVED_PROFILE_MODEL","captured":0}
    ledger=Path(ledger)
    keys=set()
    if ledger.exists():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record=json.loads(line)
                keys.add((str(record["game_id"]),record["model"],record["captured_utc"]))
    captured=[]
    for g in future:
        home=profiles["teams"].get(str(g["home_id"]))
        away=profiles["teams"].get(str(g["away_id"]))
        f=matchup_features(home,away) if home and away else None
        p=predict(base,g)
        if f is None or p is None:
            continue
        data={"features":f,"base_margin":float(p["pred_margin"]),
              "base_total":float(p["pred_total"])}
        for variant in VARIANTS:
            trained=report["frozen_historical_coefficients"][variant]
            obj=adjusted(data,trained)
            ph=(obj["total"]+obj["margin"])/2
            pa=(obj["total"]-obj["margin"])/2
            rec={
                "game_id":str(g["game_id"]),"home_id":str(g["home_id"]),
                "away_id":str(g["away_id"]),"tip_utc":g["tip_utc"],
                "captured_utc":iso(now),"training_latest_tip":base["training_latest_tip"],
                "model":VERSION_BY_VARIANT[variant],"variant":variant,
                "champion_version":SOURCE_VERSION,"base_prior_weight":PRIOR_WEIGHT,
                "research_only":True,"observed_boxscore_source_asof":True,
                "historical_training_source_publication_authenticated":False,
                "pred_home":round(ph,2),"pred_away":round(pa,2),
                "pred_margin":round(obj["margin"],2),
                "pred_total":round(obj["total"],2),
                "base_margin":float(p["pred_margin"]),
                "base_total":float(p["pred_total"]),
                "home_box_games":home.get("boxscore_games_current"),
                "away_box_games":away.get("boxscore_games_current"),
            }
            key=str(g["game_id"]),rec["model"],rec["captured_utc"]
            if key not in keys and validate_frozen(rec,g,now):
                keys.add(key)
                captured.append(rec)
    if captured:
        ledger.parent.mkdir(parents=True,exist_ok=True)
        with ledger.open("a",encoding="utf-8") as out:
            for rec in captured:
                out.write(json.dumps(rec,sort_keys=True)+"\n")
    return {"status":"GENUINE_PRETIP_RESEARCH_FORECAST",
            "captured":len(captured),
            "games":len({r["game_id"] for r in captured}),
            "feature_covered_upcoming_games":len({r["game_id"] for r in captured})}


def _quote_asof(lines,g,cutoff):
    """Require actual book quote observed before BOTH model captures."""
    selected=None
    for q in lines:
        if (not validate_quote(q,g) or
                utc(q["captured_utc"])>cutoff):
            continue
        priority=(int("fanduel" in q["provider"].lower()),utc(q["captured_utc"]))
        if selected is None or priority>selected[0]:
            selected=(priority,q)
    return selected[1] if selected else None


def _winloss(predicted,actual):
    if predicted==0 or actual==0:
        return "P"
    return "W" if (predicted>0)==(actual>0) else "L"


def _market_metrics(events):
    def one(key):
        outcomes=[r[key] for r in events if key in r]
        w=outcomes.count("W"); l=outcomes.count("L"); p=outcomes.count("P")
        return {"wins":w,"losses":l,"pushes":p,"n":len(outcomes),
                "win_pct_ex_push":round(w/(w+l),4) if w+l else None}
    def avg(field):
        values=[r[field] for r in events if field in r]
        return round(sum(values)/len(values),3) if values else None
    return {"matched_market_games":len(events),
            "ats_with_min3pt_model_edge":one("ats"),
            "ou_with_min4pt_model_edge":one("ou"),
            "model_margin_mae":avg("model_margin_error"),
            "market_margin_mae":avg("market_margin_error"),
            "model_total_mae":avg("model_total_error"),
            "market_total_mae":avg("market_total_error"),
            "quotes_are_not_verified_closing_lines":True,
            "sportsbook_roi_not_available":True}


def grade(games,verified,now,base_path,shadow_path,market_path,year=None):
    year=year or now.year+int(now.month>=7)
    d1={str(x) for x in verified}
    champion=frozen_v04(games,base_path,now,d1,year)
    by_id={str(g["game_id"]):g for g in games}
    candidate_latest={}
    path=Path(shadow_path)
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            p=json.loads(line)
            gid=str(p.get("game_id"));g=by_id.get(gid)
            if (g is None or gid not in champion or
                    not final_game(g,year,d1) or not eligible(g,now,False) or
                    not validate_frozen(p,g,now)):
                continue
            if abs((dt(p["captured_utc"])-
                    dt(champion[gid]["captured_utc"])).total_seconds())>7200:
                continue
            key=gid,p["variant"]
            if key not in candidate_latest or candidate_latest[key]["captured_utc"]<p["captured_utc"]:
                candidate_latest[key]=p
    quotes=[]
    market=Path(market_path)
    if market.exists():
        for line in market.read_text(encoding="utf-8").splitlines():
            if line.strip():
                quotes.append(json.loads(line))
    by_quote=defaultdict(list)
    for q in quotes:
        by_quote[str(q.get("game_id"))].append(q)
    results={}
    for variant in VARIANTS:
        observations,reference=[],[]
        market_obs=[]
        for (gid,variant_name),p in candidate_latest.items():
            if variant_name!=variant:
                continue
            game=by_id[gid]; original=champion[gid]
            observations.append(model_observation(game,p,12))
            reference.append(model_observation(game,original,12))
            qt=_quote_asof(by_quote[gid],game,min(
                utc(p["captured_utc"]),utc(original["captured_utc"])))
            if qt is not None:
                m=float(game["home_score"])-float(game["away_score"])
                total=float(game["home_score"])+float(game["away_score"])
                row={"game_id":gid,"provider":qt["provider"]}
                if qt.get("home_spread") is not None:
                    spread=float(qt["home_spread"])
                    edge=float(p["pred_margin"])+spread
                    row["model_margin_error"]=abs(float(p["pred_margin"])-m)
                    row["market_margin_error"]=abs(-spread-m)
                    if abs(edge)>=3:
                        row["ats"]=_winloss(edge,m+spread)
                if qt.get("total") is not None:
                    line_total=float(qt["total"])
                    edge=float(p["pred_total"])-line_total
                    row["model_total_error"]=abs(float(p["pred_total"])-total)
                    row["market_total_error"]=abs(line_total-total)
                    if abs(edge)>=4:
                        row["ou"]=_winloss(edge,total-line_total)
                market_obs.append(row)
        results[variant]={
            "frozen_pairs":len(observations),
            "v04":metrics(reference),
            "challenger":metrics(observations),
            "source_observed_market":_market_metrics(market_obs)}
        if results[variant]["v04"]["n"]!=results[variant]["challenger"]["n"]:
            raise AssertionError("Unmatched v04 and challenger games")
    return {
        "status":"GENUINE_FROZEN_V05_MATCHUP_RESEARCH",
        "models":results,"minimum_pairs_for_review":MIN_PAIRED_PROSPECTIVE,
        "promotion_gate":"INSUFFICIENT_PROSPECTIVE_ATS_OU_VALIDATION",
        "original_v04_predictions_unchanged":True,
        "warning":("Matchup model and book quote must both have genuine pretip "
                   "captures. Book line precedes both forecast captures. "
                   "No historical ATS/O-U inferred from final scores or "
                   "unverified historical sportsbook markets; no ROI claim."),
    }


def run(root="data",now=None,historical=False):
    root=Path(root); now=now or utcnow()
    try:
        verified={str(x) for x in load_directory(root/"team_directory.json")}
    except (OSError,ValueError):
        verified=set()
    games=read_csv(root/"games.csv")
    accepted=load_accepted(root/"advanced"/"boxscore_team_games.jsonl")
    hist=root/"reports"/"matchup_v05_backtest_2026.json"
    if historical:
        result=retrospective(games,accepted,verified)
        hist.parent.mkdir(parents=True,exist_ok=True)
        hist.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n",
                        encoding="utf-8")
        return {"status":result["status"],"selection":result.get("selected_on_january"),
                "counts":result.get("counts"),"february":result.get("february")}
    if hist.exists():
        selected=json.loads(hist.read_text(encoding="utf-8"))
    else:
        selected={"status":"BLOCKED_MISSING_HISTORICAL_MODEL"}
    frozen=freeze(games,accepted,verified,now,selected,
                  root/"matchup_v05_shadow_ledger.jsonl")
    grade_out=grade(games,verified,now,root/"forecast_ledger.jsonl",
                   root/"matchup_v05_shadow_ledger.jsonl",
                   root/"market"/"pretip_quotes.jsonl")
    output={"as_of_utc":iso(now),"active_model":SOURCE_VERSION,
            "source_line_not_closing":True,
            "historical_validation_selected":selected.get("selected_on_january"),
            "freeze":frozen,"grade":grade_out,
            "website_champion_not_promoted":True}
    dest=root/"reports"/"matchup_v05_prospective_2027.json"
    dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(output,indent=2,sort_keys=True)+"\n",
                    encoding="utf-8")
    return {"freeze":frozen,"graded":grade_out}


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--root",default="data")
    parser.add_argument("--historical",action="store_true")
    args=parser.parse_args()
    print(json.dumps(run(args.root,historical=args.historical),indent=2))
