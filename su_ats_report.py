"""Print observed SU accuracy *within* ATS losses, wins and no-edge passes.

Runs entirely against genuinely frozen, previously saved predictions and
book quotes. Never re-fits or backfills any outcomes or old sportsbook lines.
"""
import json
from pathlib import Path

from cbb import read_csv, utcnow, iso
from team_directory import load_directory
from possession_simulator_v05 import grade

def run(root="data", now=None):
    root=Path(root)
    now=now or utcnow()
    games=read_csv(root/"games.csv")
    d1=set(str(x) for x in load_directory(root/"team_directory.json"))
    report=grade(games,d1,now,
                 root/"forecast_ledger.jsonl",
                 root/"possession_v05_shadow_ledger.jsonl",
                 root/"market"/"pretip_quotes.jsonl")
    market=report["results"]["blend20"]["market_relative_context"]
    return {
        "as_of_utc":iso(now),
        "featured_model":"CBB_V05_POSSESSION_EVENTS_20261113_BLEND20",
        "frozen_pairs":report["results"]["blend20"]["frozen_pairs"],
        "quoted_games":market["matched_market_games"],
        "total_ats_su_breakdown":market["summary"]["su_by_ats_result"],
        "by_market_spread":{
            name:{
                "quoted_games":section["matched_source_quotes"],
                "su_by_ats_result":section["su_by_ats_result"],
            } for name,section in market["by_market_spread"].items()
        },
        "limitations":("Genuinely frozen pre-tip as-observed prices only; "
                       "book lines may not be closing lines. SU correctness "
                       "is the winner's side, not the profitability or "
                       "available odds of a hypothetical moneyline wager."),
    }

if __name__=="__main__":
    print("CBB_SU_ATS_DIAGNOSTIC "+json.dumps(run(),sort_keys=True))
