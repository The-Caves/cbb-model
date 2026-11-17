"""Seeded possession-event simulator and pre-tip sportsbook audit regression."""
import json
from datetime import datetime,timedelta,timezone

import numpy as np

from cbb import ACTIVE_LIVE_VERSION
from possession_simulator_v05 import (
    DRAWS, MIN_TRAIN, VARIANTS, VERSION, observation_shots, fit_shots,
    matchup_rate,context,simulate,apply,train,analyze,
    valid_forecast,freeze,grade,
)
UTC=timezone.utc
TIP=datetime(2026,11,18,23,tzinfo=UTC)
def iso(x):return x.isoformat().replace("+00:00","Z")

def box(gid):
    return {"game_id":str(gid),"home_id":"1","away_id":"2",
            "season":2027,"tip_utc":iso(TIP-timedelta(days=4+gid)),
            "source_captured_utc":iso(TIP-timedelta(days=3+gid)),
            "home":{"fgm":26,"fga":59,"three_m":8,"three_a":21,
                    "ftm":14,"fta":19},
            "away":{"fgm":24,"fga":60,"three_m":7,"three_a":20,
                    "ftm":13,"fta":17}}

def profile(n=5,boxes=4,pace=70):
    p={"current_d1_games":n,"boxscore_games_current":boxes}
    for k,v in {"pace_est":pace,"tov_pct":.16,"orb_pct":.31,
                "ft_rate":.27,"three_rate":.36}.items():
        p[k+"_for_adj"]=v;p[k+"_allowed_adj"]=v
    return p

def game(gid="F",final=False):
    return {"game_id":gid,"tip_utc":iso(TIP),"season":2027,
            "home_id":"1","away_id":"2","home":"A","away":"B",
            "neutral":"0","home_score":"82" if final else "",
            "away_score":"68" if final else "",
            "completed":"1" if final else "0",
            "score_conflict":"0","identity_conflict":"0",
            "first_final_observed_utc":iso(TIP+timedelta(hours=5)) if final else ""}

def test_shot_decomposition_and_opponent_estimation():
    assert observation_shots(box(1),"home")=={
        "two":(18.,38.),"three":(8.,21.),"free":(14.,19.)}
    bad=box(1);bad["home"]["three_a"]=65
    try:observation_shots(bad,"home");assert False
    except ValueError:pass
    shot=fit_shots([box(i) for i in range(1,7)],2027)
    assert shot["current_box_games"]["1"]==6
    assert shot["current_box_games"]["2"]==6
    assert 0<matchup_rate(shot,"three","1","2")<1
    assert matchup_rate(shot,"three","1","999") is None

def test_simulation_is_seed_reproducible_shooting_sensitive_and_nonsynthetic_on_missing():
    shot=fit_shots([box(i) for i in range(1,5)],2027)
    ctx=context(profile(),profile(pace=73),shot,"1","2",True)
    assert ctx is not None
    g=game();moment=TIP-timedelta(hours=10)
    x=simulate(g,moment,ctx); y=simulate(g,moment,ctx)
    assert x==y and x["draws"]==DRAWS
    assert x["simulated_margin_sd"]>0 and x["simulated_total_sd"]>0
    assert x["home_components"]["field_goal_attempts"]>0
    higher={**ctx,"home":{**ctx["home"],"three":.49}}
    assert simulate(g,moment,higher)["pred_home"]>x["pred_home"]
    assert abs((x["pred_home"]-x["pred_away"])-x["pred_margin"])<1e-8
    assert abs((x["pred_home"]+x["pred_away"])-x["pred_total"])<1e-8
    assert context(profile(boxes=1),profile(),shot,"1","2",False) is None
    assert context({**profile(),"orb_pct_for_adj":None},profile(),
                   shot,"1","2",False) is None

def test_learned_calibration_is_conservative_and_sparse_data_pass():
    p={"pred_margin":50.,"pred_total":200.}
    from possession_simulator_v05 import maturity_confidence
    sample={"physical":p,"base_margin":3.,"base_total":140.,
            "actual_margin":7.,"actual_total":151.,
            "confidence":maturity_confidence(profile(),profile())}
    assert train([sample]) is None
    rows=[{**sample,"base_margin":float((i%8)-4)}
          for i in range(MIN_TRAIN+10)]
    fitted=train(rows)
    assert fitted["n_training"]==len(rows)
    for axis in ("margin","total"):
        assert 0<=fitted[axis]["slope"]<=.65
    for variant in VARIANTS:
        x=apply(sample,variant,fitted)
        assert abs(x["margin"]-3)<=12+1e-8
        assert abs(x["total"]-140)<=15+1e-8
    report=analyze([],{},{"1","2"})
    assert report["status"]=="INSUFFICIENT_HISTORICAL_DATA"
    assert report["active_model_unchanged"] is True
    assert report["selected_in_january"] is None
    assert report["source_timestamps_in_historical_archive_authenticated"] is False

def test_freeze_requires_source_and_trained_snapshot(tmp_path):
    dest=tmp_path/"sim.jsonl"
    result=freeze([game()],{},{"1","2"},TIP-timedelta(hours=2),
                  {"status":"INSUFFICIENT_HISTORICAL_DATA"},dest)
    assert result["captured"]==0 and not dest.exists()

def test_real_pretip_quote_must_precede_both_forecasts(tmp_path):
    from market_capture import SOURCE
    g=game("G",final=True);now=TIP+timedelta(hours=8)
    base=tmp_path/"v04.jsonl";shadow=tmp_path/"sim.jsonl";books=tmp_path/"book.jsonl"
    champion={"model":ACTIVE_LIVE_VERSION,"game_id":"G",
             "home_id":"1","away_id":"2","tip_utc":g["tip_utc"],
             "captured_utc":iso(TIP-timedelta(hours=3)),
             "pred_home":79,"pred_away":70,"pred_margin":9,"pred_total":149,
             "home_current_games":6,"away_current_games":6}
    cand={"game_id":"G","home_id":"1","away_id":"2","tip_utc":g["tip_utc"],
          "model":VERSION["blend20"],"variant":"blend20",
          "captured_utc":iso(TIP-timedelta(hours=4)),
          "training_latest_tip":iso(TIP-timedelta(days=1)),
          "champion_version":ACTIVE_LIVE_VERSION,"previous_season_weight":.15,
          "n_simulations":DRAWS,"research_only":True,
          "pred_home":83,"pred_away":72,"pred_margin":11,"pred_total":155,
          "home_current_games":6,"away_current_games":6}
    q={"game_id":"G","home_id":"1","away_id":"2","tip_utc":g["tip_utc"],
       "captured_utc":iso(TIP-timedelta(hours=6)),
       "source":SOURCE,"provider":"DraftKings","home_spread":-7.5,
       "total":150.5,"spread_derived_from_explicit_favorite_flags":True}
    base.write_text(json.dumps(champion)+"\n")
    shadow.write_text(json.dumps(cand)+"\n")
    books.write_text(json.dumps(q)+"\n")
    good=grade([g],{"1","2"},now,base,shadow,books)["results"]["blend20"]
    assert good["frozen_pairs"]==1
    assert good["v04"]["n"]==good["simulator"]["n"]==1
    assert good["same_quote_simulator"]["ats_with_min3pt_model_edge"]["wins"]==1
    assert good["same_quote_simulator"]["ou_with_min4pt_model_edge"]["losses"]==1
    audit=good["market_relative_context"]
    assert audit["matched_market_games"]==1
    assert audit["completed_frozen_pairs"]==1
    assert audit["market_quote_coverage_pct"]==100.0
    assert audit["summary"]["matched_source_quotes"]==1
    assert audit["summary"]["challenger"]["matched_market_games"]==1
    assert audit["by_market_spread"]["mid_spread_6_to_under15"]["matched_source_quotes"]==1
    assert audit["by_source_provider"]["DraftKings"]["matched_source_quotes"]==1
    # Legacy pre-tip shadow records did not contain shot-game counts.
    # We label them unknown instead of inventing mature data coverage.
    assert audit["by_model_boxscore_sample"]["boxscore_coverage_unavailable"]["matched_source_quotes"]==1
    # Older simulator shadow rows also lack optional season sample metadata.
    # They must not cause the entire scheduled pipeline to fail.
    legacy={k:v for k,v in cand.items() if k not in (
        "home_current_games","away_current_games")}
    shadow.write_text(json.dumps(legacy)+"\n")
    historic=grade([g],{"1","2"},now,base,shadow,books)["results"]["blend20"]
    assert historic["frozen_pairs"]==1
    assert historic["v04"]["n"]==historic["simulator"]["n"]==1
    assert historic["same_quote_simulator"]["matched_market_games"]==1
    books.write_text(json.dumps({**q,"captured_utc":iso(TIP-timedelta(hours=2))})+"\n")
    no_quote=grade([g],{"1","2"},now,base,shadow,books)["results"]["blend20"]
    assert no_quote["same_quote_simulator"]["matched_market_games"]==0
    assert no_quote["market_relative_context"]["market_quote_coverage_pct"]==0.0
    after={**cand,"captured_utc":iso(TIP+timedelta(seconds=1))}
    shadow.write_text(json.dumps(after)+"\n")
    assert not valid_forecast(after,g,now)
    assert grade([g],{"1","2"},now,base,shadow,books)["results"]["blend20"]["frozen_pairs"]==0


def test_split_total20_uses_lighter_margin_and_only_simulated_total():
    from possession_simulator_v05 import VERSION
    row={"physical":{"pred_margin":22.,"pred_total":170.},
         "base_margin":7.,"base_total":146.}
    v=apply(row,"split_total20")
    blend=apply(row,"blend20")
    assert v["margin"]==7.0
    assert abs(v["total"]-150.8)<1e-8
    assert blend["margin"]==10.0
    assert v["total"]==blend["total"]
    assert VERSION["split_total20"] != VERSION["blend20"]
    assert VERSION["blend20"]=="CBB_V05_POSSESSION_EVENTS_20261113_BLEND20"


def test_maturity_weight_uses_dated_boxscore_and_opponent_strength():
    from possession_simulator_v05 import maturity_confidence, maturity_weight
    h={**profile(n=3,boxes=2),
       "opponent_adjusted_strength_points":8,
       "schedule_strength_avg_opponent_points":-2}
    a={**profile(n=3,boxes=2),
       "opponent_adjusted_strength_points":-1,
       "schedule_strength_avg_opponent_points":-4}
    early=maturity_confidence(h,a)
    assert early["opponent_quality_available"]
    assert early["opponent_stepup_points"]==12
    early_weight=maturity_weight(early)
    assert 0.10<=early_weight<0.2
    mature=maturity_confidence({**h,"current_d1_games":12,
           "boxscore_games_current":10},
           {**a,"current_d1_games":12,"boxscore_games_current":10})
    later=maturity_weight(mature)
    assert later>early_weight
    assert 0.10<=later<=0.40
    harder=maturity_confidence({**h,"current_d1_games":12,
           "boxscore_games_current":10,"opponent_adjusted_strength_points":29},
           {**a,"current_d1_games":12,"boxscore_games_current":10})
    assert maturity_weight(harder)<later
    unknown=maturity_confidence(profile(n=12,boxes=10),
                                profile(n=12,boxes=10))
    assert unknown["opponent_stepup_points"] is None
    assert unknown["opponent_quality_available"] is False
    assert maturity_weight(unknown)<maturity_weight({
        **unknown,"opponent_quality_available":True,
        "opponent_stepup_points":0})


def test_adaptive_model_preserves_featured_blend_and_requires_real_evidence():
    from possession_simulator_v05 import maturity_confidence, maturity_weight, VERSION
    evidence=maturity_confidence(profile(n=8,boxes=6),
                                 profile(n=9,boxes=7))
    row={"physical":{"pred_margin":18.,"pred_total":180.},
         "base_margin":7.,"base_total":148.,"confidence":evidence}
    result=apply(row,"adaptive_maturity")
    weight=maturity_weight(evidence)
    assert abs(result["margin"]-(7.+weight*11.))<1e-6
    assert abs(result["total"]-(148.+weight*32.))<1e-6
    assert VERSION["adaptive_maturity"] != VERSION["blend20"]
    assert VERSION["blend20"]=="CBB_V05_POSSESSION_EVENTS_20261113_BLEND20"
    rec={
        "model":VERSION["adaptive_maturity"],"variant":"adaptive_maturity",
        "game_id":"F","home_id":"1","away_id":"2","tip_utc":iso(TIP),
        "captured_utc":iso(TIP-timedelta(hours=4)),
        "training_latest_tip":iso(TIP-timedelta(days=1)),
        "champion_version":ACTIVE_LIVE_VERSION,
        "previous_season_weight":.15,"n_simulations":DRAWS,
        "research_only":True,"pred_home":82,"pred_away":72,
        "pred_margin":10,"pred_total":154,
        "source_maturity_confidence":evidence,"adaptive_weight":weight,
    }
    assert valid_forecast(rec,game(),TIP-timedelta(hours=3))
    assert not valid_forecast({**rec,"adaptive_weight":weight+.03},
                              game(),TIP-timedelta(hours=3))
    assert not valid_forecast({k:v for k,v in rec.items()
                              if k!="source_maturity_confidence"},
                              game(),TIP-timedelta(hours=3))



def test_context_market_groups_are_same_game_and_never_invent_lines():
    from possession_simulator_v05 import market_relative_slices
    from matchup_v05 import _market_metrics
    def pack(gid,spread,provider,hbox,abox):
        quote={"provider":provider,"home_spread":spread,"total":148.5}
        model={"game_id":gid,"provider":provider,
               "model_margin_error":6.5,"market_margin_error":7.0,
               "model_total_error":7.0,"market_total_error":8.0,
               "ats":"W","ou":"L"}
        base={**model,"model_margin_error":7.4,"model_total_error":7.5}
        return {"quote":quote,"game":{"tip_utc":"2026-11-17T20:00:00Z"},
                "snapshot":{"home_shot_games":hbox,"away_shot_games":abox},
                "v04":base,"research":model}
    rows=[pack("a",-3.5,"ESPN BET",2,8),
          pack("b",-11.5,"DraftKings",8,8),
          pack("c",-22.5,"DraftKings",9,9),
          pack("d",None,"ESPN BET",None,None)]
    result=market_relative_slices(rows,6)
    assert result["matched_market_games"]==4
    assert result["market_quote_coverage_pct"]==66.67
    assert result["summary"]["margin_mae_delta_vs_v04"]==-0.9
    assert result["summary"]["total_mae_delta_vs_market"]==-1.0
    for name in ("close_spread_under6","mid_spread_6_to_under15",
                 "large_spread_15plus","spread_unavailable"):
        assert result["by_market_spread"][name]["matched_source_quotes"]==1
    assert result["by_source_provider"]["ESPN BET"]["matched_source_quotes"]==2
    assert result["by_model_boxscore_sample"]["limited_boxscore_sample_under5"]["matched_source_quotes"]==1
    assert result["by_model_boxscore_sample"]["boxscore_coverage_unavailable"]["matched_source_quotes"]==1
    assert result["by_game_date_utc"]["2026-11-17"]["matched_source_quotes"]==4
