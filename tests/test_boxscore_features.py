"""Free, strict, source-backed Four Factors and possession collection tests."""
import copy
import json
from datetime import datetime, timezone, timedelta

import pytest
from boxscore_features import BoxscoreGap, choose_games, decode_side, parse_summary, profiles, run

UTC=timezone.utc


def game(gid="44",h="1",a="2",tip="2026-11-02T19:00:00Z"):
    return {"game_id":gid,"tip_utc":tip,"season":2027,"home_id":h,"away_id":a,
       "home_score":"81","away_score":"72","completed":"1"}


def stats(fg="28-60",three="9-24",ft="16-20",orb="10",drb="25",to="12"):
    return {"statistics":[{"name":k,"displayValue":v} for k,v in (
      ("fieldGoalsMade-fieldGoalsAttempted",fg),
      ("threePointFieldGoalsMade-threePointFieldGoalsAttempted",three),
      ("freeThrowsMade-freeThrowsAttempted",ft),
      ("offensiveRebounds",orb),("defensiveRebounds",drb),
      ("totalTurnovers",to))]}


def source():
    return {"header":{"id":"44","competitions":[{"competitors":[
          {"id":"1","score":"81"},{"id":"2","score":"72"}]}]},
      "boxscore":{"teams":[{"team":{"id":"1"},**stats()},
                             {"team":{"id":"2"},**stats(fg="25-59",three="8-23",ft="14-17",orb="9",drb="24",to="11")}]}}


def test_genuine_boxscore_metrics():
    r=parse_summary(source(),game(),"2026-11-03T00:00:00Z","deadbeef")
    assert r["home"]["efg_pct"]==pytest.approx((28+4.5)/60,abs=0.0001)
    assert r["home"]["poss_est"]==pytest.approx(71.5)
    assert r["away"]["orb_pct"]==pytest.approx(9/(9+25),abs=.0001)
    assert 80 < r["home"]["off_rtg"] < 130
    assert r["status"]=="RESEARCH_OBSERVED_POSTGAME"


def test_missing_stats_cannot_be_invented():
    p=source()
    del p["boxscore"]["teams"][0]["statistics"][0]
    with pytest.raises(BoxscoreGap,match="field goals"):
        parse_summary(p,game(),"2026-11-03T00:00:00Z","x")


def test_reject_source_identity_and_score_mismatch():
    p=source();p["header"]["id"]="other"
    with pytest.raises(BoxscoreGap,match="ID mismatch"):
        parse_summary(p,game(),"2026-11-03T00:00:00Z","x")
    p=source();p["header"]["competitions"][0]["competitors"][0]["score"]="92"
    with pytest.raises(BoxscoreGap,match="score conflict"):
        parse_summary(p,game(),"2026-11-03T00:00:00Z","x")
    p=source();p["boxscore"]["teams"].pop()
    with pytest.raises(BoxscoreGap,match="two-sided"):
        parse_summary(p,game(),"2026-11-03T00:00:00Z","x")


def test_out_of_range_and_fake_stats_rejected():
    with pytest.raises(BoxscoreGap,match="three-point totals"):
        decode_side(stats(fg="18-40",three="23-30"))
    with pytest.raises(BoxscoreGap,match="made exceeds"):
        decode_side(stats(fg="70-50"))
    with pytest.raises(BoxscoreGap,match="missing"):
        decode_side({"statistics":[]})


def test_historical_cutoff_and_selected_games():
    now=datetime(2026,11,3,23,tzinfo=UTC)
    candidate=game()
    future=game("45",tip="2026-11-03T22:00:00Z")
    seen=game("46",tip="2026-11-01T19:00:00Z")
    picked=choose_games([candidate,future,seen],{"46":{}},now,20,0)
    assert [g["game_id"] for g in picked]==["44"]


def test_profiles_exclude_unverified_opponents():
    r=parse_summary(source(),game(),"2026-11-03T00:00:00Z","x")
    p=profiles({"44":r},2027,d1_ids={"1"})
    assert p=={}
    p=profiles({"44":r},2027,d1_ids={"1","2"})
    assert p["1"]["status"]=="INSUFFICIENT"
    assert p["1"]["games"]==1


def test_collector_idempotence_and_rejection(tmp_path):
    from cbb import write_csv
    root=tmp_path/"data"
    root.mkdir()
    old=game()
    write_csv(root/"games.csv",[old])
    # Synthetic verified-directory fixture; production does not infer divisions.
    (root/"team_directory.json").write_text(json.dumps({"teams":{str(i):"Synthetic test school "+str(i) for i in range(1,301)}}))
    def opener(req,timeout=None):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return json.dumps(source()).encode()
        return Response()
    now=datetime(2026,11,3,23,tzinfo=UTC)
    first=run(root,max_games=1,delay=0,opener=opener,now=now)
    second=run(root,max_games=1,delay=0,opener=opener,now=now)
    assert first["accepted_this_run"]==1
    assert second["attempted"]==0
    assert first["model_unchanged"] is True
    assert (root/"advanced/raw_team_boxscores/2027/44.json.gz").exists()


def test_long_backfill_uses_historical_quota():
    from cbb_data_quality import select_games
    now=datetime(2026,11,4,18,tzinfo=UTC)
    games=[]
    for i in range(100):
        for year in (2025,2026,2027):
            day=datetime(year-1,11,1,18,tzinfo=UTC)+timedelta(days=i)
            games.append({"game_id":str(year*1000+i),"tip_utc":day.strftime('%Y-%m-%dT%H:%M:%SZ'),
                          "season":year,"home_id":"1","away_id":"2",
                          "completed":1,"home_score":83,"away_score":68})
    selected=choose_games(games,set(),now,max_games=80,d1_ids={"1","2"})
    sizes={yr:sum(int(g["season"])==yr for g in selected) for yr in (2025,2026,2027)}
    assert sizes=={2025:24,2026:44,2027:12}
    assert len(selected)==len({g["game_id"] for g in selected})==80

def test_retry_only_on_transient_error(monkeypatch):
    import io, urllib.error
    from boxscore_features import fetch_summary
    attempts=[]
    def fake_opener(req, timeout):
        attempts.append(1)
        if len(attempts)==1:
            raise urllib.error.HTTPError(req.full_url,503,"Service Unavailable",{},None)
        return io.BytesIO(b'{"header":{}}')
    monkeypatch.setattr('boxscore_features.time.sleep',lambda _:None)
    response,raw=fetch_summary("44",opener=fake_opener)
    assert len(attempts)==2 and response=={"header":{}} and raw

def test_commit_checkpoint_does_not_duplicate_across_runs(tmp_path):
    from cbb import write_csv
    from boxscore_features import load_accepted
    from test_boxscore_features import source
    import io
    root=tmp_path/"data";root.mkdir()
    start=datetime(2026,11,2,19,tzinfo=UTC)
    records=[game(gid=str(i),tip=start.strftime('%Y-%m-%dT%H:%M:%SZ')) for i in ("44","45")]
    write_csv(root/"games.csv",records)
    (root/"team_directory.json").write_text(json.dumps(
        {"teams":{str(i):"Fixture "+str(i) for i in range(1,301)}}))
    calls=[]
    def fake_opener(req,timeout):
        gid=req.full_url.split("=")[-1]
        calls.append(gid)
        p=source()
        p["header"]["id"]=gid
        return io.BytesIO(json.dumps(p).encode())
    now=datetime(2026,11,4,18,tzinfo=UTC)
    first=run(root,max_games=1,delay=0,opener=fake_opener,now=now)
    second=run(root,max_games=1,delay=0,opener=fake_opener,now=now)
    assert len(calls)==2 and set(calls)=={"44","45"}
    assert first["accepted_this_run"]==second["accepted_this_run"]==1
    assert len(load_accepted(root/"advanced/boxscore_team_games.jsonl"))==2
