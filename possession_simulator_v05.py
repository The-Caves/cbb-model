"""2026 CBB possession-event simulator built on evidence-backed team boxscores.

Estimate field-goal volume, 2P/3P attempts and makes, missed-shot offensive
rebounds, turnovers, and free throws conditional on opponent defense. Fit
two-way shooting rates only from accepted D-I games known before the cutoff.
Calibrate or blend with *lighter* (0.15 previous-season weight) score ridge.

A simulation run is an approximation, not a true play-by-play simulation:
events are sampled at aggregate game level and rate uncertainty remains.
All historic source records were fetched retrospectively. No authenticated
historical 2025 betting-line claims or automatic promotion.
"""
import argparse
import hashlib
import json
import math
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import numpy as np

from cbb import ACTIVE_LIVE_VERSION, dt, eligible, fit, iso, predict, read_csv, utcnow
from boxscore_features import load_accepted
from matchup_v05 import (_market_metrics, _market_row, _quote_asof,
                         asof_profiles, scores)
from team_directory import load_directory
from team_intelligence import matched_boxscores
from v05_transition_challenger import final_game, frozen_v04, metrics, model_observation

VERSION_PREFIX = "CBB_V05_POSSESSION_EVENTS_20261113"
VARIANTS = ("pure", "blend20", "blend40", "trained_blend")
VERSION = {variant:VERSION_PREFIX+"_"+variant.upper() for variant in VARIANTS}
LAST_SEASON_SHOTS = 0.08
SHOOTING_SHRINK = {"two":450.0, "three":375.0, "free":240.0}
MIN_TRAIN, MIN_VALIDATION, MIN_HOLDOUT = 200, 100, 100
MIN_PROSPECTIVE = 150
DRAWS = 512
SEED_TAG = "v05_possession_events_seed_v1"
BLEND = {"pure":1.0,"blend20":0.2,"blend40":0.4}
REQUIRED = ("pace_est","tov_pct","orb_pct","ft_rate","three_rate")


def observation_shots(rec, side):
    """Source-verified attempts and makes, including true 2P not inferred eFG."""
    raw=rec[side]
    fg=float(raw["fgm"]); fga=float(raw["fga"])
    three=float(raw["three_m"]); three_a=float(raw["three_a"])
    ft=float(raw["ftm"]); fta=float(raw["fta"])
    two_a=fga-three_a
    two_m=fg-three
    if (min(two_a,two_m,three,three_a,ft,fta)<0 or
        two_m>two_a or three>three_a or ft>fta or
        not all(math.isfinite(v) for v in (two_a,two_m,three,three_a,ft,fta))):
        raise ValueError("Impossible 2P/3P/FT source record")
    return {"two":(two_m,two_a), "three":(three,three_a),
            "free":(ft,fta)}


def fit_shots(records,year):
    """Shrink as-of 2P/3P/FT percentages by attempts and opponent.

    Historical last-season player groups have changed; their shot weights
    receive only 8% of an equivalent current-season game. Defensive FT%
    effect is disabled because FT accuracy largely belongs to the shooter.
    """
    data=defaultdict(list)
    attempts=defaultdict(lambda:defaultdict(float))
    games=defaultdict(set)
    for rec in records:
        h,a=str(rec["home_id"]),str(rec["away_id"])
        prior=int(rec["season"])!=year
        weight=LAST_SEASON_SHOTS if prior else 1.0
        try:
            for own,opp,side in ((h,a,"home"),(a,h,"away")):
                parts=observation_shots(rec,side)
                for kind,(made,count) in parts.items():
                    if count<=0:continue
                    data[kind].append((own,opp,made,count,weight))
                    if not prior:
                        attempts[own][kind]+=count
                if not prior:games[own].add(str(rec["game_id"]))
        except (KeyError,ValueError,TypeError,OverflowError):
            # Fail closed: never partly admit a malformed boxscore.
            continue
    fit={}
    for kind in ("two","three","free"):
        rows=data[kind]
        if not rows:
            return None
        count=sum(w*a for _,_,_,a,w in rows)
        if count<=0:return None
        league=sum(w*m for _,_,m,_,w in rows)/count
        attack,allowed={},{}
        for _ in range(8):
            numerator=defaultdict(float); denominator=defaultdict(float)
            for own,opp,m,a,w in rows:
                if kind=="free":
                    target=m-league*a
                else:
                    target=m-a*(league+allowed.get(opp,0.0))
                numerator[own]+=w*target
                denominator[own]+=w*a
            attack={team:n/(denominator[team]+SHOOTING_SHRINK[kind])
                    for team,n in numerator.items()}
            if kind!="free":
                numerator=defaultdict(float);denominator=defaultdict(float)
                for own,opp,m,a,w in rows:
                    numerator[opp]+=w*(m-a*(league+attack.get(own,0.0)))
                    denominator[opp]+=w*a
                allowed={team:n/(denominator[team]+SHOOTING_SHRINK[kind])
                         for team,n in numerator.items()}
        fit[kind]={"league":float(league),
                   "attack":attack,"allowed":allowed}
    return {"shooting":fit,"current_attempts":attempts,
            "current_box_games":{t:len(ids) for t,ids in games.items()},
            "accepted_boxscores":len(records),
            "retrospective_source_time_authenticated":False}


def matchup_rate(fit,kind,team,opponent):
    if team not in fit["current_box_games"] or opponent not in fit["current_box_games"]:
        return None
    slot=fit["shooting"][kind]
    if team not in slot["attack"]:
        return None
    if kind!="free" and opponent not in slot["allowed"]:
        return None
    raw=slot["league"]+slot["attack"][team]+(
        slot["allowed"].get(opponent,0) if kind!="free" else 0)
    low,high={"two":(.28,.68),"three":(.19,.49),"free":(.58,.89)}[kind]
    return float(np.clip(raw,low,high))


def context(home,away,shots,home_id,away_id,venue):
    if home is None or away is None or shots is None:return None
    if min(int(home.get("current_d1_games",0)),
           int(away.get("current_d1_games",0)))<3:
        return None
    if min(int(home.get("boxscore_games_current",0)),
           int(away.get("boxscore_games_current",0)))<2:
        return None
    tid_h,tid_a=str(home_id),str(away_id)
    if min(shots["current_box_games"].get(tid_h,0),
           shots["current_box_games"].get(tid_a,0))<2:
        return None
    values={}
    try:
        for team,other,own,opp in ((tid_h,tid_a,home,away),
                                    (tid_a,tid_h,away,home)):
            two=matchup_rate(shots,"two",team,other)
            three=matchup_rate(shots,"three",team,other)
            free=matchup_rate(shots,"free",team,other)
            if None in (two,three,free):return None
            rates={}
            for factor in REQUIRED:
                off=float(own[factor+"_for_adj"])
                allowed=float(opp[factor+"_allowed_adj"])
                rates[factor]=(off+allowed)/2
            if not all(math.isfinite(x) for x in rates.values()):
                return None
            rates["two"]=two;rates["three"]=three;rates["free"]=free
            rates["tov_pct"]=float(np.clip(rates["tov_pct"],.07,.29))
            rates["orb_pct"]=float(np.clip(rates["orb_pct"],.13,.48))
            rates["ft_rate"]=float(np.clip(rates["ft_rate"],.10,.57))
            rates["three_rate"]=float(np.clip(rates["three_rate"],.18,.60))
            rates["pace_est"]=float(np.clip(rates["pace_est"],50,92))
            values[team]=rates
        pace=float(np.clip(
            (values[tid_h]["pace_est"]+values[tid_a]["pace_est"])/2,52,88))
        return {"pace":pace,"home":values[tid_h],"away":values[tid_a],
                "home_id":tid_h,"away_id":tid_a,
                "venue":float(venue),
                "home_games":int(home["current_d1_games"]),
                "away_games":int(away["current_d1_games"]),
                "home_shot_games":shots["current_box_games"][tid_h],
                "away_shot_games":shots["current_box_games"][tid_a]}
    except (KeyError,ValueError,TypeError,OverflowError):
        return None


def _score_events(rng,possessions,rates):
    """Stochastic aggregate shot-chain approximation consistent in expectation.

    E(FGA) comes from possessions = FGA - ORB + TO + .475 FTA,
    with ORB estimated from missed FG volume times offensive rebounding%.
    """
    tov=rng.binomial(possessions,rates["tov_pct"])
    frac3=rates["three_rate"]
    miss=1-((1-frac3)*rates["two"]+frac3*rates["three"])
    orb_from_fga=miss*rates["orb_pct"]
    denominator=max(.50,1-orb_from_fga+.475*rates["ft_rate"])
    attempts=(possessions-tov)/denominator
    fga=rng.poisson(np.maximum(attempts,1e-5))
    three_a=rng.binomial(fga,frac3)
    two_a=fga-three_a
    three_m=rng.binomial(three_a,rates["three"])
    two_m=rng.binomial(two_a,rates["two"])
    ft_a=rng.poisson(fga*rates["ft_rate"])
    ft_m=rng.binomial(ft_a,rates["free"])
    points=2*two_m+3*three_m+ft_m
    return points,{"turnovers":float(np.mean(tov)),
                   "field_goal_attempts":float(np.mean(fga)),
                   "three_attempts":float(np.mean(three_a)),
                   "free_throw_attempts":float(np.mean(ft_a)),
                   "offensive_rebound_est":float(np.mean(
                       (fga-two_m-three_m)*rates["orb_pct"]))}


def simulate(game,asof,matchup,draws=DRAWS):
    """Deterministic RNG from version/game ID/cutoff; no result-based seeds."""
    if matchup is None or draws<64:return None
    raw=(SEED_TAG+"|"+str(game["game_id"])+"|"+iso(asof)).encode()
    seed=int.from_bytes(hashlib.sha256(raw).digest()[:8],"big")
    rng=np.random.default_rng(seed)
    n=np.clip(np.rint(rng.normal(matchup["pace"],4.5,draws)),45,95).astype(int)
    home,home_events=_score_events(rng,n,matchup["home"])
    away,away_events=_score_events(rng,n,matchup["away"])
    # Fixed total 3.5-point home-venue baseline (neutral games get zero).
    if matchup["venue"]:
        home=home+1.75
        away=away-1.75
    margin=home-away
    total=home+away
    return {"pred_home":float(np.mean(home)),
            "pred_away":float(np.mean(away)),
            "pred_margin":float(np.mean(margin)),
            "pred_total":float(np.mean(total)),
            "simulated_margin_sd":float(np.std(margin)),
            "simulated_total_sd":float(np.std(total)),
            "simulated_home_win_rate":float(np.mean(margin>0)),
            "pace":float(np.mean(n)),
            "home_components":home_events,"away_components":away_events,
            "draws":int(draws),"seed_version":SEED_TAG,
            "as_of_utc":iso(asof)}


def apply(row,variant,calibration=None):
    p=row["physical"]
    if variant in BLEND:
        weight=BLEND[variant]
        shift_m=shift_t=0.0
    elif variant=="trained_blend":
        if not calibration:raise ValueError("Untrained proposed blend")
        weight=None
        shift_m=calibration["margin"]["intercept"]+(
            calibration["margin"]["slope"]*
            (p["pred_margin"]-row["base_margin"]))
        shift_t=calibration["total"]["intercept"]+(
            calibration["total"]["slope"]*
            (p["pred_total"]-row["base_total"]))
    else:
        raise ValueError("Unknown physical model version")
    if weight is not None:
        shift_m=weight*(p["pred_margin"]-row["base_margin"])
        shift_t=weight*(p["pred_total"]-row["base_total"])
    # Severe expected-rate drift may indicate a missing historical feature;
    # never let low-coverage simulations dominate a source-backed baseline.
    shift_m=float(np.clip(shift_m,-12,12))
    shift_t=float(np.clip(shift_t,-15,15))
    return {"margin":row["base_margin"]+shift_m,
            "total":row["base_total"]+shift_t}


def train(rows):
    if len(rows)<MIN_TRAIN:return None
    def fit_axis(name):
        base_key="base_"+name
        target_key="actual_"+name
        physical_key="pred_"+name
        x=np.array([r["physical"][physical_key]-r[base_key] for r in rows])
        y=np.array([r[target_key]-r[base_key] for r in rows])
        centered=x-x.mean()
        # Regularized one-parameter blend on training years, no January or
        # February optimization, and no unconstrained overfit to huge swings.
        slope=float(np.clip(np.dot(centered,y-y.mean())/
                            (np.dot(centered,centered)+900),0,0.65))
        intercept=float(np.clip(y.mean()-slope*x.mean(),-2,2))
        return {"slope":slope,"intercept":intercept}
    return {"margin":fit_axis("margin"),"total":fit_axis("total"),
            "n_training":len(rows),
            "training_window":"2025-11_to_2025-12"}


def make_examples(games,accepted,verified,year=2026,draws=DRAWS):
    verified={str(x) for x in verified}
    groups=defaultdict(list)
    for g in games:
        if (final_game(g,year,verified) and
                f"{year-1}-11-01"<=g["tip_utc"][:10]<f"{year}-03-01"):
            groups[g["tip_utc"][:10]].append(g)
    out=[];skipped=defaultdict(int)
    for day in sorted(groups):
        cutoff=dt(day+"T00:00:00Z")
        try:
            base,profiles=asof_profiles(games,accepted,verified,cutoff,year,True)
            champion=fit(games,cutoff,test_season=year,
                         verified_ids=verified,retrospective=True,
                         prior_weight=.30,alpha=1.5)
            box=matched_boxscores(games,accepted,cutoff,year,verified,True)
            shooting=fit_shots(box,year)
        except ValueError:
            skipped["missing_asof_model"]+=len(groups[day])
            continue
        if not shooting or profiles.get("status")!="RETROSPECTIVE_RESEARCH_ONLY":
            skipped["unavailable_boxscore_history"]+=len(groups[day])
            continue
        for g in groups[day]:
            home=profiles["teams"].get(str(g["home_id"]))
            away=profiles["teams"].get(str(g["away_id"]))
            match=context(home,away,shooting,g["home_id"],g["away_id"],
                          str(g.get("neutral","0")) not in ("1","true","True"))
            p=predict(base,g)
            v=predict(champion,g)
            if match is None or p is None or v is None:
                skipped["insufficient_game_matchup"]+=1
                continue
            physical=simulate(g,cutoff,match,draws)
            if physical is None:
                skipped["invalid_physical_distribution"]+=1
                continue
            out.append({
                "game_id":str(g["game_id"]),"day":day,
                "physical":physical,
                "base_margin":float(p["pred_margin"]),
                "base_total":float(p["pred_total"]),
                "v04_margin":float(v["pred_margin"]),
                "v04_total":float(v["pred_total"]),
                "actual_margin":float(g["home_score"])-float(g["away_score"]),
                "actual_total":float(g["home_score"])+float(g["away_score"]),
                "base_margin_sd":float(base["margin_sd"]),
                "min_current_games":min(match["home_games"],match["away_games"]),
                "min_box_games":min(match["home_shot_games"],match["away_shot_games"]),
            })
    return out,{
        "days_considered":len(groups),"eligible_d1_finals":sum(map(len,groups.values())),
        "matched_games":len(out),"excluded":dict(skipped),
        "retrospective_source_timing_authenticated":False}


def analyze(games,accepted,verified,year=2026,draws=DRAWS):
    rows,coverage=make_examples(games,accepted,verified,year,draws)
    training=[r for r in rows if r["day"]<f"{year}-01-01"]
    validation=[r for r in rows if f"{year}-01-01"<=r["day"]<f"{year}-02-01"]
    heldout=[r for r in rows if f"{year}-02-01"<=r["day"]<f"{year}-03-01"]
    report={
        "status":"INSUFFICIENT_HISTORICAL_DATA","model_family":VERSION_PREFIX,
        "active_model_unchanged":True,
        "lighter_carryover_prior":.15,
        "variants":VERSION,
        "draws_per_game":draws,
        "mechanism":("As-of opponent-adjusted shooting rates; pace and turnovers "
                     "set FGA opportunities, miss*ORB increases possessions' "
                     "shot volume, then 2PA/3PA and FTA lead to stochastic points."),
        "counts":{"training":len(training),"validation":len(validation),
                  "historical_heldout":len(heldout)},
        "coverage":coverage,"january":{},"february":{},
        "selected_in_january":None,
        "source_timestamps_in_historical_archive_authenticated":False,
        "historical_book_lines_available":False,
        "february_previously_examined_by_project":True,
        "promotion_gate":"NEEDS_NEW_PROSPECTIVE_ATS_OU_EVIDENCE",
        "warning":("Retrospective ESPN boxscore acquisition; historical scores "
                   "are filtered to pre-cutoff tip times but capture availability "
                   "in 2025 not independently established. Already inspected "
                   "February 2026 is not an untouched holdout. Simulator is "
                   "aggregate-event approximation, not full play-by-play."),
    }
    if len(training)<MIN_TRAIN:return report
    calibration=train(training)
    report["frozen_training_calibration"]=calibration
    if len(validation)<MIN_VALIDATION:return report
    def evaluate(split):
        outcomes={
            "v04":scores([{**r,"base_margin":r["v04_margin"],
                            "base_total":r["v04_total"]} for r in split],
                            lambda r:{"margin":r["base_margin"],"total":r["base_total"]}),
            "lighter":scores(split,lambda r:{
                "margin":r["base_margin"],"total":r["base_total"]}),
        }
        for variant in VARIANTS:
            outcomes[variant]=scores(split,lambda r,v=variant:
                                     apply(r,v,calibration))
        return outcomes
    report["january"]=evaluate(validation)
    lighter=report["january"]["lighter"]
    selected=[
        k for k in VARIANTS if (
            report["january"][k]["margin_mae"]<=lighter["margin_mae"]-0.03 or
            report["january"][k]["total_mae"]<=lighter["total_mae"]-0.08)
        and report["january"][k]["su_accuracy"]>=lighter["su_accuracy"]-.005
        and report["january"][k]["brier"]<=lighter["brier"]+.0015]
    report["selected_in_january"]=(min(selected,key=lambda k:(
        report["january"][k]["margin_mae"]+
        .5*report["january"][k]["total_mae"],k)) if selected else None)
    if len(heldout)<MIN_HOLDOUT:return report
    report["february"]=evaluate(heldout)
    report["february_strata"]={
        "near_pickem_under6":evaluate(
            [r for r in heldout if abs(r["base_margin"])<6]),
        "big_favorite_12plus":evaluate(
            [r for r in heldout if abs(r["base_margin"])>=12]),
        "low_shot_sample_under5":evaluate(
            [r for r in heldout if r["min_box_games"]<5]),
    }
    report["status"]="HISTORICAL_SCORE_REPLAY_COMPLETE"
    chosen=report["selected_in_january"]
    if chosen:
        base,win=report["february"]["lighter"],report["february"][chosen]
        report["february_selected_delta_vs_lighter"]={
            "margin_mae_improvement":round(base["margin_mae"]-win["margin_mae"],3),
            "total_mae_improvement":round(base["total_mae"]-win["total_mae"],3),
            "su_pp_change":round(100*(win["su_accuracy"]-base["su_accuracy"]),2),
        }
    return report


def valid_forecast(rec,g,now):
    try:
        variant=rec["variant"]
        if variant not in VARIANTS or rec["model"]!=VERSION[variant]:
            return False
        capture=dt(rec["captured_utc"]);tip=dt(g["tip_utc"])
        if (capture>now or not capture<tip or
                tip-capture>timedelta(hours=72) or
                rec["tip_utc"]!=g["tip_utc"] or
                str(rec["game_id"])!=str(g["game_id"]) or
                str(rec["home_id"])!=str(g["home_id"]) or
                str(rec["away_id"])!=str(g["away_id"]) or
                dt(rec["training_latest_tip"])>=capture or
                rec["champion_version"]!=ACTIVE_LIVE_VERSION or
                rec["previous_season_weight"]!=.15 or
                rec["n_simulations"]!=DRAWS or
                not rec.get("research_only",False)):
            return False
        return (abs(float(rec["pred_home"])-float(rec["pred_away"])-
                    float(rec["pred_margin"]))<.035 and
                abs(float(rec["pred_home"])+float(rec["pred_away"])-
                    float(rec["pred_total"]))<.035)
    except (ValueError,TypeError,KeyError,OverflowError):
        return False


def freeze(games,accepted,verified,now,historical,ledger):
    year=now.year+int(now.month>=7)
    d1={str(x) for x in verified}
    targets=[g for g in games if int(g["season"])==year
             and str(g["home_id"]) in d1 and str(g["away_id"]) in d1
             and str(g.get("score_conflict","0")) not in ("1","true","True")
             and str(g.get("identity_conflict","0")) not in ("1","true","True")
             and now<dt(g["tip_utc"])<=now+timedelta(hours=72)]
    if not targets:
        return {"status":"NO_UPCOMING_VERIFIED_D1_GAMES","captured":0}
    if historical.get("status")!="HISTORICAL_SCORE_REPLAY_COMPLETE":
        return {"status":"BLOCKED_NO_HISTORICAL_TRAINING","captured":0}
    calibration=historical.get("frozen_training_calibration")
    if not calibration:
        return {"status":"BLOCKED_NO_TRAINED_CALIBRATION","captured":0}
    try:
        base,profiles=asof_profiles(games,accepted,d1,now,year,False)
        box=matched_boxscores(games,accepted,now,year,d1,False)
        shooting=fit_shots(box,year)
    except ValueError:
        return {"status":"BLOCKED_NO_CURRENT_SOURCE_EVIDENCE","captured":0}
    if shooting is None:return {"status":"BLOCKED_NO_SHOOTING_HISTORY","captured":0}
    ledger=Path(ledger);existing=set()
    if ledger.exists():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            if line.strip():
                obj=json.loads(line)
                existing.add((str(obj["game_id"]),obj["model"],obj["captured_utc"]))
    captured=[]
    for g in targets:
        home=profiles["teams"].get(str(g["home_id"]))
        away=profiles["teams"].get(str(g["away_id"]))
        match=context(home,away,shooting,g["home_id"],g["away_id"],
                      str(g.get("neutral","0")) not in ("1","true","True"))
        pred=predict(base,g)
        if match is None or pred is None:continue
        sim=simulate(g,now,match)
        if sim is None:continue
        row={"physical":sim,"base_margin":float(pred["pred_margin"]),
             "base_total":float(pred["pred_total"])}
        for variant in VARIANTS:
            result=apply(row,variant,calibration)
            home_pts=(result["total"]+result["margin"])/2
            away_pts=(result["total"]-result["margin"])/2
            rec={
                "model":VERSION[variant],"variant":variant,
                "champion_version":ACTIVE_LIVE_VERSION,
                "game_id":str(g["game_id"]),
                "home_id":str(g["home_id"]),"away_id":str(g["away_id"]),
                "tip_utc":g["tip_utc"],"captured_utc":iso(now),
                "training_latest_tip":base["training_latest_tip"],
                "research_only":True,"previous_season_weight":.15,
                "n_simulations":DRAWS,
                "simulated_pace":round(sim["pace"],3),
                "simulated_margin_sd":round(sim["simulated_margin_sd"],3),
                "simulated_total_sd":round(sim["simulated_total_sd"],3),
                "home_current_games":match["home_games"],
                "away_current_games":match["away_games"],
                "home_shot_games":match["home_shot_games"],
                "away_shot_games":match["away_shot_games"],
                "pred_home":round(home_pts,2),"pred_away":round(away_pts,2),
                "pred_margin":round(result["margin"],2),
                "pred_total":round(result["total"],2),
            }
            key=str(g["game_id"]),rec["model"],rec["captured_utc"]
            if key not in existing and valid_forecast(rec,g,now):
                captured.append(rec);existing.add(key)
    if captured:
        ledger.parent.mkdir(parents=True,exist_ok=True)
        with ledger.open("a",encoding="utf-8") as f:
            for rec in captured:f.write(json.dumps(rec,sort_keys=True)+"\n")
    return {"status":"PRETIP_POSSESSION_SIMULATION_RESEARCH",
            "captured":len(captured),
            "unique_games":len({r["game_id"] for r in captured})}


def grade(games,verified,now,champion_path,sim_path,market_path):
    from market_capture import utc
    year=now.year+int(now.month>=7)
    d1={str(x) for x in verified}
    champion=frozen_v04(games,champion_path,now,d1,year)
    by_id={str(g["game_id"]):g for g in games}
    saved={}
    sim_path=Path(sim_path)
    if sim_path.exists():
        for line in sim_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():continue
            rec=json.loads(line)
            gid=str(rec.get("game_id"));g=by_id.get(gid)
            if (g is None or gid not in champion or
                    not final_game(g,year,d1) or
                    not eligible(g,now,False) or
                    not valid_forecast(rec,g,now)):
                continue
            if abs((dt(rec["captured_utc"])-
                    dt(champion[gid]["captured_utc"])).total_seconds())>7200:
                continue
            key=gid,rec["variant"]
            if key not in saved or saved[key]["captured_utc"]<rec["captured_utc"]:
                saved[key]=rec
    quotes=defaultdict(list)
    market_path=Path(market_path)
    if market_path.exists():
        for line in market_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row=json.loads(line)
                quotes[str(row.get("game_id"))].append(row)
    results={}
    for variant in VARIANTS:
        champ_data=[];sim_data=[];book_champ=[];book_sim=[]
        for (gid,name),rec in saved.items():
            if name!=variant:continue
            g=by_id[gid];reference=champion[gid]
            champ_data.append(model_observation(g,reference,12))
            sim_data.append(model_observation(g,rec,12))
            quote=_quote_asof(quotes[gid],g,min(
                utc(rec["captured_utc"]),utc(reference["captured_utc"])))
            if quote:
                book_champ.append(_market_row(g,reference,quote))
                book_sim.append(_market_row(g,rec,quote))
        results[variant]={
            "frozen_pairs":len(sim_data),
            "v04":metrics(champ_data),"simulator":metrics(sim_data),
            "same_quote_v04":_market_metrics(book_champ),
            "same_quote_simulator":_market_metrics(book_sim)}
        if results[variant]["v04"]["n"]!=results[variant]["simulator"]["n"]:
            raise AssertionError("Different graded outcome denominators")
    return {
        "status":"SOURCE_FROZEN_MATCHED_SIMULATOR_RESEARCH",
        "versions":VERSION,"results":results,
        "minimum_pairs_for_review":MIN_PROSPECTIVE,
        "website_model_unchanged":True,
        "promotion_gate":"INSUFFICIENT_INDEPENDENT_PRETIP_ATS_OU",
        "note":("Each same-game simulator and v04 forecast was frozen before "
                "tip within 2h, using the same real market quote observed "
                "before both. No reconstructed historical sportsbook lines, "
                "ROI inference or automatic model promotion.")}


def run(root="data",historical=False,now=None):
    root=Path(root)
    now=now or utcnow()
    try:
        verified={str(x) for x in load_directory(root/"team_directory.json")}
    except (OSError,ValueError):
        verified=set()
    games=read_csv(root/"games.csv")
    accepted=load_accepted(root/"advanced"/"boxscore_team_games.jsonl")
    history=root/"reports"/"possession_simulator_v05_backtest_2026.json"
    if historical:
        result=analyze(games,accepted,verified)
        history.parent.mkdir(parents=True,exist_ok=True)
        history.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n",
                           encoding="utf-8")
        return {"status":result["status"],
                "selected":result.get("selected_in_january"),
                "counts":result.get("counts"),"february":result.get("february")}
    try:
        report=json.loads(history.read_text(encoding="utf-8"))
    except (OSError,ValueError):
        report={"status":"MISSING_TRAINING"}
    frozen=freeze(games,accepted,verified,now,report,
                  root/"possession_v05_shadow_ledger.jsonl")
    evaluated=grade(games,verified,now,
                    root/"forecast_ledger.jsonl",
                    root/"possession_v05_shadow_ledger.jsonl",
                    root/"market"/"pretip_quotes.jsonl")
    out={"as_of_utc":iso(now),"frozen":frozen,"performance":evaluated,
         "historical_selection":report.get("selected_in_january"),
         "promoted":False,"active_champion":ACTIVE_LIVE_VERSION}
    target=root/"reports"/"possession_simulator_v05_prospective_2027.json"
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(out,indent=2,sort_keys=True)+"\n",
                      encoding="utf-8")
    return out


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--root",default="data")
    parser.add_argument("--historical",action="store_true")
    args=parser.parse_args()
    print(json.dumps(run(args.root,historical=args.historical),indent=2))
