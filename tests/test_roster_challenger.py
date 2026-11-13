"""Roster prior challenger must pass closed and not alter deployed v0.4."""
from datetime import datetime, timedelta, timezone

import pytest

from cbb import fit, predict
from roster_challenger import (
    adjusted_prior_multipliers, freeze, grade, valid, VERSION, PREVIOUS_VERSION
)

UTC=timezone.utc
BASE=datetime(2026,11,4,19,tzinfo=UTC)
def iso(d): return d.isoformat().replace("+00:00","Z")

def game(i,d,hs=75,ats=65,completed=True):
    return {"game_id":str(i),"tip_utc":iso(d),"season":2027,
            "home_id":"1","away_id":"2","home":"A","away":"B",
            "home_score":str(hs) if completed else "",
            "away_score":str(ats) if completed else "",
            "neutral":"0", "completed":str(int(completed)),
            "score_conflict":"0","identity_conflict":"0",
            "first_final_observed_utc":iso(d+timedelta(hours=5)) if completed else ""}


def test_multiplier_requires_verified_roster_evidence():
    verified={"1":{"status":"RESEARCH_SAMPLE_NOT_FULL_SEASON",
                   "retained_minutes_share":0.25,"prior_games_verified":20,
                   "prior_player_game_coverage":1.0},
              "2":{"status":"UNKNOWN_INSUFFICIENT_PRIOR_PLAYER_GAMES",
                   "retained_minutes_share":None,"prior_games_verified":2},
              "3":{"status":"RESEARCH_SAMPLE_NOT_FULL_SEASON",
                   "retained_minutes_share":1,"prior_games_verified":5,
                   "prior_player_game_coverage":1.0}}
    weights=adjusted_prior_multipliers(verified)
    assert weights["1"] == 0.55
    assert "2" not in weights
    assert weights["3"] == 1.0


def test_no_roster_evidence_produces_no_model_forecast(tmp_path):
    future=game("next", BASE+timedelta(days=2),completed=False)
    ledger=tmp_path/"roster.jsonl"
    p=freeze([future],{"1","2"},[],[],ledger,now=BASE)
    assert p["captured"]==0
    assert p["status"]=="PASS_INSUFFICIENT_ROSTER_COVERAGE"
    assert not ledger.exists()
    g=grade([future],{"1","2"},tmp_path/"champion.jsonl",ledger,now=BASE)
    assert g["v04"]["n"]==g["roster_candidate"]["n"]==0
    assert g["promotion_gate"]=="INSUFFICIENT_PAIRED_GAMES"


def test_champion_fit_is_unchanged_without_explicit_roster_opt_in():
    rows=[game(i,BASE+timedelta(days=i),hs=70+i*2,ats=68) for i in range(8)]
    cutoff=BASE+timedelta(days=12)
    original=fit(rows,cutoff,verified_ids={"1","2"},
                 retrospective=True,alpha=1.5,prior_weight=.30)
    opt_in_empty=fit(rows,cutoff,verified_ids={"1","2"},
                     retrospective=True,alpha=1.5,prior_weight=.30,
                     prior_team_multipliers={})
    assert predict(original,game("future",cutoff,completed=False)) == \
           predict(opt_in_empty,game("future",cutoff,completed=False))
    assert original["roster_adjusted_prior"] is False
    assert opt_in_empty["roster_adjusted_prior"] is True
    with pytest.raises(ValueError):
        fit(rows,cutoff,retrospective=True,
            prior_team_multipliers={"1":-1})


def test_frozen_candidate_must_have_pretip_roster_and_training_evidence():
    tip=BASE+timedelta(days=10)
    now=tip-timedelta(hours=2)
    g=game("future",tip,completed=False)
    p={"model":VERSION,"game_id":"future","tip_utc":iso(tip),
       "captured_utc":iso(now),"home_id":"1","away_id":"2",
       "pred_home":70,"pred_away":65,"pred_margin":5,"pred_total":135,
       "verified_roster_teams":["1","2"],
       "training_latest_tip":iso(now-timedelta(days=1))}
    assert valid(p,g,now)
    assert VERSION != PREVIOUS_VERSION
    assert not valid({**p,"model":PREVIOUS_VERSION},g,now)
    assert not valid({**p,"captured_utc":iso(tip+timedelta(minutes=2))},g,now)
    assert not valid({**p,"verified_roster_teams":["1"]},g,now)
    assert not valid({**p,"pred_margin":22},g,now)


def test_roster_weight_is_conservative_under_incomplete_player_coverage():
    profiles={
        "1":{"status":"RESEARCH_SAMPLE_NOT_FULL_SEASON",
             "retained_minutes_share":0.0,"prior_games_verified":5,
             "prior_player_game_coverage":0.20},
        "2":{"status":"RESEARCH_SAMPLE_NOT_FULL_SEASON",
             "retained_minutes_share":0.0,"prior_games_verified":5,
             "prior_player_game_coverage":0.10},
        "3":{"status":"RESEARCH_SAMPLE_NOT_FULL_SEASON",
             "retained_minutes_share":0.0,"prior_games_verified":20,
             "prior_player_game_coverage":0.80},
    }
    adjusted=adjusted_prior_multipliers(profiles)
    assert adjusted["1"] == 0.95
    assert "2" not in adjusted
    assert adjusted["3"] == 0.4


def test_prior_weight_is_scoring_team_specific_not_shared_average():
    # This catches a former pitfall: averaging both teams' roster multipliers
    # applies the same discount regardless of which team's roster changed.
    prior_tip=datetime(2025,11,1,20,tzinfo=UTC)
    current_tip=datetime(2026,11,1,20,tzinfo=UTC)
    past=[{**game("p"+str(i),prior_tip+timedelta(days=i),
                  hs=102,ats=53),"season":2026} for i in range(16)]
    recent=[game("c"+str(i),current_tip+timedelta(days=i),
                 hs=73,ats=70) for i in range(4)]
    cutoff=datetime(2026,11,10,0,tzinfo=UTC)
    variants=[]
    for multipliers in ({"1":0.4,"2":1.0},{"1":1.0,"2":0.4}):
        model=fit(past+recent,cutoff,test_season=2027,
                  retrospective=True,verified_ids={"1","2"},
                  alpha=1.5,prior_weight=0.30,
                  prior_team_multipliers=multipliers)
        variants.append(predict(model,game("next",cutoff,completed=False)))
    # If both sides had used the *average* multiplier (0.7), this would
    # incorrectly produce identical predictions for these two distinct rosters.
    assert abs(variants[0]["pred_total"] - variants[1]["pred_total"]) > 0.05
