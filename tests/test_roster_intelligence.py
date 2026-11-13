"""Guards for ESPN source-backed player and roster continuity evidence."""
import hashlib
from datetime import datetime, timedelta, timezone
import json
import pytest

from roster_intelligence import (
    PlayerEvidenceGap, continuity, player_boxscore,
    roster_snapshot, read_jsonl, collect_current_rosters,
    upcoming_teams, plan_player_backfill
)

UTC=timezone.utc
TIP=datetime(2026,3,10,18,tzinfo=UTC)
CAPTURE=datetime(2026,3,11,15,tzinfo=UTC)
def stamp(x):
    return x.isoformat().replace("+00:00","Z")

def game():
    return {"game_id":"112", "home_id":"1", "away_id":"2",
            "home_score":"55", "away_score":"55",
            "tip_utc":stamp(TIP), "season":2026}

def player_payload():
    blocks=[]
    for team, offset in (("1",0),("2",100)):
        athletes=[]
        for n in range(5):
            athletes.append({"athlete":{"id":str(4000+offset+n)},
                             "stats":["40:00","11","2","1"]})
        blocks.append({"team":{"id":team},"statistics":[{
            "names":["MIN","PTS","REB","AST"],"athletes":athletes}]})
    return {"header":{"id":"112"}, "boxscore":{"players":blocks}}

def valid_player_record():
    return player_boxscore(player_payload(),game(),stamp(CAPTURE),"a"*64)

def test_verified_player_minutes_scores_and_stable_ids():
    r=valid_player_record()
    assert len(r["players"]) == 10
    assert sum(p["minutes"] for p in r["players"] if p["team_id"]=="1") == 200
    assert sum(p["points"] for p in r["players"] if p["team_id"]=="2") == 55
    assert r["source_captured_utc"] == stamp(CAPTURE)
    assert r["status"] == "SOURCE_OBSERVED_AFTER_FINAL"

def test_missing_stats_id_invalid_minutes_or_score_is_not_imputed():
    payload=player_payload()
    payload["boxscore"]["players"][0]["statistics"][0]["athletes"][0]["stats"][0]="DNP"
    with pytest.raises(PlayerEvidenceGap):
        player_boxscore(payload,game(),stamp(CAPTURE),"a"*64)
    payload=player_payload()
    payload["boxscore"]["players"][1]["statistics"][0]["athletes"][1]["athlete"]["id"]="4000"
    # Identical athlete ID may be a transfer but cannot play for two schools.
    with pytest.raises(PlayerEvidenceGap):
        player_boxscore(payload,game(),stamp(CAPTURE),"a"*64)
    payload=player_payload()
    payload["header"]["id"]="wrong"
    with pytest.raises(PlayerEvidenceGap):
        player_boxscore(payload,game(),stamp(CAPTURE),"a"*64)

def test_roster_source_and_membership_are_timestamped():
    payload={"team":{"id":"1","athletes":[{"id":str(4000+i)} for i in range(12)]}}
    roster=roster_snapshot(payload,"1","2026-11-13T12:00:00Z","d"*64)
    assert roster["season"]==2027
    assert not roster["historical_membership_authenticated"]
    assert roster["player_ids"][0] == "4000"
    with pytest.raises(PlayerEvidenceGap):
        roster_snapshot(payload,"2","2026-11-13T12:00:00Z","d"*64)

def test_returns_from_observed_prior_minutes_and_marks_unknown_team():
    original=valid_player_record()
    games=[]
    for i in range(5):
        rec={**original,"game_id":str(100+i)}
        # Every game is observed before next-season roster capture.
        games.append(rec)
    current=[{"team_id":"1","season":2027,
              "source_captured_utc":"2026-11-13T12:00:00Z",
              "player_ids":[str(4000+i) for i in range(3)]+
                            ["4100","4101","8000","8001","8002","8003","8004"]},
             {"team_id":"2","season":2027,
              "source_captured_utc":"2026-11-13T12:00:00Z",
              "player_ids":[str(4100+i) for i in range(2)]+
                            [str(9000+i) for i in range(8)]}]
    result=continuity(games,current,datetime(2026,11,13,18,tzinfo=UTC),
                      2027,{"1","2"})
    a=result["1"]
    assert a["status"] == "RESEARCH_SAMPLE_NOT_FULL_SEASON"
    assert a["retained_minutes_share"] == .6
    assert a["retained_points_share"] == .6
    assert a["transfers_with_prior_verified_minutes"] == 2
    assert a["prior_games_verified"] == 5
    b=result["2"]
    assert b["retained_minutes_share"] == .4
    late=continuity(games,current,datetime(2026,11,12,18,tzinfo=UTC),
                    2027,{"1","2"})
    assert late == {}
    no_history=continuity([],current,datetime(2026,11,13,18,tzinfo=UTC),
                          2027,{"1","2"})
    assert no_history["1"]["retained_minutes_share"] is None

def test_only_verified_directory_and_fail_closed_on_missing_file(tmp_path):
    assert read_jsonl(tmp_path/"missing.jsonl") == []
    (tmp_path/"dupe.jsonl").write_text(
        json.dumps({"game_id":"x"})+"\n"+json.dumps({"game_id":"x"})+"\n")
    with pytest.raises(PlayerEvidenceGap):
        read_jsonl(tmp_path/"dupe.jsonl", unique_key=lambda x: x["game_id"])
    # A missing fixture is not a real roster.
    assert continuity([],[],datetime(2026,11,13,tzinfo=UTC),2027,{"1"}) == {}


def test_roster_collection_prioritizes_next_verified_matchups():
    now=datetime(2026,11,13,18,tzinfo=UTC)
    def g(i,delta,h,a):
        return {"game_id":str(i),"tip_utc":stamp(now+timedelta(hours=delta)),
                "home_id":str(h),"away_id":str(a),
                "score_conflict":"0","identity_conflict":"0"}
    games=[g(1,26,2,3),g(2,5,1,2),g(3,7,9,999),g(4,-1,3,4)]
    assert upcoming_teams(games,now,{"1","2","3","4","9"}) == ["1","2","3"]


def test_player_backfill_targets_prior_minute_coverage_not_random_archive():
    def g(gid,season,h,a):
        return {"game_id":str(gid),"season":season,
                "home_id":str(h),"away_id":str(a),
                "tip_utc":f"2026-03-{gid:02d}T00:00:00Z"}
    previous=[g(i,2026,"1","2") for i in range(1,9)]
    unrelated=[g(i,2026,"3","4") for i in range(9,13)]
    archived=[{"game_id":str(i),"season":2026,"home_id":"1","away_id":"2"}
              for i in (1,2)]
    plan=plan_player_backfill(unrelated+previous,archived,["1"],2027,4)
    assert len(plan)==4
    assert all("1" in (x["home_id"],x["away_id"]) for x in plan[:3])
    assert len({x["game_id"] for x in plan})==4


def test_collection_report_uses_actual_post_capture_cutoff(tmp_path,monkeypatch):
    import roster_intelligence as ri
    real_now=datetime.now(UTC)
    earlier=real_now-timedelta(minutes=10)
    monkeypatch.setattr(ri,"load_directory",lambda path: {"1":"Source Team"})
    def fake_collection(root,max_teams,now):
        dest=tmp_path/"advanced"/"roster_snapshots.jsonl"
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_text(json.dumps({
            "season":real_now.year+int(real_now.month>=7),
            "team_id":"1",
            "source_captured_utc":stamp(real_now),
            "player_ids":[str(5500+i) for i in range(12)]
        })+"\n")
        return {"accepted":1}
    monkeypatch.setattr(ri,"collect_current_rosters",fake_collection)
    ri.run(tmp_path, now=earlier, collect=True)
    report=json.loads((tmp_path/"advanced"/
                       f"roster_continuity_{real_now.year+int(real_now.month>=7)}.json").read_text())
    assert report["roster_teams_recent"]==1
    assert report["teams"]["1"]["status"]=="UNKNOWN_INSUFFICIENT_PRIOR_PLAYER_GAMES"
    assert ri.dt(report["as_of_utc"]) >= real_now
