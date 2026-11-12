"""Authentic pre-tip ESPN odds: no invented lines, prices or history."""
import json
from datetime import datetime, timezone
from market_capture import (normalize, append_pretip, last_pretip_quotes,
                            validate_quote, grade_market)

OBS="2026-11-12T22:00:00Z"
TIP="2026-11-13T00:00:00Z"
CUTOFF="2026-11-13T09:00:00Z"

def payload(provider="ESPN BET", spread=6.5, total=147.5, flag_home=True, flag_away=False):
    return {"events":[{"id":"100","date":TIP,"competitions":[{
        "competitors":[
            {"homeAway":"home","team":{"id":"1","displayName":"Home Lions"}},
            {"homeAway":"away","team":{"id":"2","displayName":"Away Hawks"}}],
        "odds":[{"provider":{"id":"19","name":provider},"spread":spread,
            "overUnder":total,
            "homeTeamOdds":{"favorite":flag_home},
            "awayTeamOdds":{"favorite":flag_away}}]}]}]}

def game():
    return {"game_id":"100","tip_utc":TIP,"home_id":"1","away_id":"2",
        "home":"Home Lions","away":"Away Hawks","season":2027,
        "neutral":"0","completed":1,"home_score":78,"away_score":70,
        "first_final_observed_utc":"2026-11-13T06:00:00Z"}
def prediction():
    return {"game_id":"100","tip_utc":TIP,"home_id":"1","away_id":"2",
        "home":"Home Lions","away":"Away Hawks","model":"VERSION",
        "captured_utc":OBS,"pred_margin":10.0,"pred_total":149.0}

def test_favorite_flags_sign_and_real_provider_preserved():
    q=normalize(payload(),OBS,"a"*64)
    assert len(q)==1
    assert q[0]["provider"]=="ESPN BET"
    assert q[0]["home_spread"]==-6.5 and q[0]["total"]==147.5
    assert not q[0]["closing_line_verified"]
    assert not q[0]["source_publication_time_verified"]
    assert q[0]["source_raw_sha256"]=="a"*64
    assert validate_quote(q[0],game())
    flipped=normalize(payload(flag_home=False,flag_away=True),OBS)[0]
    assert flipped["home_spread"]==6.5

def test_no_source_provider_or_ambiguous_spread_never_assumes_fanduel():
    assert normalize(payload(provider=""),OBS)==[]
    x=normalize(payload(flag_home=None,flag_away=False),OBS)
    assert len(x)==1 and x[0]["home_spread"] is None
    assert x[0]["total"]==147.5
    assert normalize(payload(spread=None,total=None),OBS)==[]
    assert normalize(payload(),"2026-11-13T00:00:00Z")==[]
    assert normalize(payload(),"2026-11-13T01:00:00Z")==[]

def test_append_only_before_tip_and_identity_required(tmp_path):
    path=tmp_path/"market"/"quotes.jsonl"
    assert append_pretip(payload(),OBS,path)["captured"]==1
    assert append_pretip(payload(),OBS,path)["captured"]==0
    assert path.read_text().count("\n")==1
    found=last_pretip_quotes(path,{"100":game()})
    assert found["100"]["home_spread"]==-6.5
    source=json.loads(path.read_text())
    source["away_id"]="3"
    assert not validate_quote(source,game())
    source["away_id"]="2";source["captured_utc"]=TIP
    assert not validate_quote(source,game())

def test_ats_ou_grading_and_no_missing_data_fabrication(tmp_path):
    path=tmp_path/"quotes.jsonl"
    append_pretip(payload(),OBS,path)
    q=last_pretip_quotes(path,{"100":game()})
    r=grade_market([game()],{"100":prediction()},q,CUTOFF,{"1":"H","2":"A"})
    assert r["overall"]["ats"]=={"w":1,"l":0,"p":0,"n":1,"win_rate":1.0}
    assert r["overall"]["ou"]=={"w":1,"l":0,"p":0,"n":1,"win_rate":1.0}
    assert r["by_day_et"]["2026-11-12"]["ats"]["n"]==1
    assert r["by_model"]["VERSION"]["ou"]["n"]==1
    assert "ESPN BET" in r["provider_counts"]
    assert grade_market([game()],{"100":prediction()},{},CUTOFF,{"1":"H","2":"A"})["matched_games"]==0
    assert grade_market([game()],{"100":prediction()},q,CUTOFF,{"1":"H"})["matched_games"]==0

def test_market_push_is_not_model_no_selection(tmp_path):
    path=tmp_path/"quotes.jsonl";append_pretip(payload(),OBS,path)
    q=last_pretip_quotes(path,{"100":game()})
    no_edge={**prediction(),"pred_margin":6.5,"pred_total":147.5}
    assert grade_market([game()],{"100":no_edge},q,CUTOFF,{"1":"H","2":"A"})["matched_games"]==0
    # At market -6.5 / total 147.5, changing the score to 76/69 gives
    # 7-point margin / 145 total: model over wins ATS, but OU loses.
    losing_total={**game(),"home_score":76,"away_score":69}
    result=grade_market([losing_total],{"100":prediction()},q,CUTOFF,{"1":"H","2":"A"})
    assert result["overall"]["ats"]["w"]==1
    assert result["overall"]["ou"]["l"]==1

def test_disputed_score_and_future_quotes_must_not_grade(tmp_path):
    path=tmp_path/"quotes.jsonl";append_pretip(payload(),OBS,path)
    q=last_pretip_quotes(path,{"100":game()})
    disputed={**game(),"score_conflict":1}
    assert grade_market([disputed],{"100":prediction()},q,CUTOFF,{"1":"H","2":"A"})["matched_games"]==0
    assert grade_market([game()],{"100":prediction()},q,OBS,{"1":"H","2":"A"})["matched_games"]==0
