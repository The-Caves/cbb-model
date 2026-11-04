"""Research coverage must be honest, reproducible and as-of safe."""
from datetime import datetime, timedelta, timezone
import pytest

from cbb_data_quality import select_games, audit, complete_before

UTC=timezone.utc
NOW=datetime(2026,11,4,3,tzinfo=UTC)
BASE=datetime(2025,11,5,19,tzinfo=UTC)


def game(i, season=2026, h="1", a="2", day=0, done=True):
    first={2025:datetime(2024,11,5,19,tzinfo=UTC),
           2026:datetime(2025,11,5,19,tzinfo=UTC),
           2027:datetime(2026,11,2,19,tzinfo=UTC)}[season]
    t=first+timedelta(days=day)
    return {"game_id":str(i),"tip_utc":t.isoformat().replace("+00:00","Z"),
            "season":season,"home_id":h,"away_id":a,
            "home_score":81 if done else "","away_score":72 if done else "",
            "completed":1 if done else 0}


def archived(g):
    return {"game_id":g["game_id"],"tip_utc":g["tip_utc"],
       "season":g["season"],"home_id":g["home_id"],"away_id":g["away_id"],
       "home":{"points":81},"away":{"points":72},
       "source_captured_utc":"2026-11-04T03:00:00Z",
       "raw_sha256":"a"*64}


def test_backfill_fair_seasons_and_strict_d1():
    rows=[]
    rows += [game(i,2027,day=0) for i in range(20)]
    rows += [game(100+i,2026,day=i) for i in range(80)]
    rows += [game(300+i,2025,day=i) for i in range(80)]
    rows += [game(999,2027,h="1",a="9999",day=0)]
    result=select_games(rows,set(),NOW,max_games=48,d1_ids={"1","2"},previous_quota=28,prior_quota=8)
    assert len(result)==48
    assert sum(g["season"]==2027 for g in result)==12
    assert sum(g["season"]==2026 for g in result)==28
    assert sum(g["season"]==2025 for g in result)==8
    assert result[12]["game_id"]=="100"
    assert result[-1]["game_id"]=="307"
    assert len({g["game_id"] for g in result})==48


def test_current_priority_and_already_ingested_not_refetched():
    rows=[game(10+i,2027,day=i) for i in range(3)]
    chosen=select_games(rows,{"12"},NOW,max_games=2,d1_ids={"1","2"},previous_quota=0,prior_quota=0)
    assert [g["game_id"] for g in chosen]==["11","10"]


def test_fails_closed_with_known_empty_directory():
    assert select_games([game(1)],set(),NOW,d1_ids=set())==[]


def test_unfinished_future_and_non_d1_have_no_training_evidence():
    row=game(1,2027,day=10)
    assert not complete_before(row,NOW)
    assert not complete_before(game(2,done=False),NOW)
    q=audit([row,game(2,done=False),game(3,h="1",a="500")],{},NOW,{"1","2"})
    assert q["by_season"]["2027"]["eligible_d1_final_games"]==0
    assert q["by_season"]["2027"]["other_or_unverified_final_games"]==1


def test_audit_reports_coverage_and_identity_spoofing():
    rows=[game(1),game(2),game(3)]
    archives={"1":archived(rows[0]),"2":archived(rows[1])}
    report=audit(rows,archives,NOW,{"1","2"})
    data=report["by_season"]["2026"]
    assert data["eligible_d1_final_games"]==3
    assert data["verified_boxscores"]==2
    assert data["missing_boxscores"]==1
    assert data["coverage_fraction"]==pytest.approx(2/3,abs=.0001)
    archives["2"]["away_id"]="WRONG"
    audited=audit(rows,archives,NOW,{"1","2"})
    assert audited["quarantined_rows"]==1
    assert audited["status"]=="QUARANTINED_SOURCE_MISMATCHES"
    assert audited["by_season"]["2026"]["verified_boxscores"]==1


def test_directory_missing_does_not_infer_team_divisions():
    report=audit([game(1)],{},NOW,set())
    assert report["status"]=="BLOCKED_NO_VERIFIED_TEAM_DIRECTORY"
    assert report["verified_d1_directory_count"]==0
    assert report["eligible_verified_d1_boxscores"]==0
