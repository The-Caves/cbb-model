"""Build static-site JSON from as-observed CBB model evidence. Never fabricate picks.

Source files are created by cbb.py. Data at rest is public game data only.
Every published forecast is a literal pre-tip ledger record; no reconstructed
postgame prediction is allowed to be labeled as a live model pick.
"""
import argparse
import json
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

from cbb import dt, utcnow, iso, read_csv, eligible, metrics, VERSION
from team_directory import clean_label, load_directory

ALLOWED_MODEL_VERSIONS = {'CBB_SCORE_RIDGE_V0_2', VERSION}  # keep all authentic historic freezes
from shadow_predictions import SHADOW_VERSION

def shadow_forecasts(ledger, by_id):
    latest={}
    ledger=Path(ledger)
    if not ledger.exists(): return latest
    for line in ledger.read_text(encoding='utf-8').splitlines():
        if not line.strip():continue
        raw=json.loads(line)
        g=by_id.get(str(raw.get('game_id')))
        if (g is None or raw.get('model')!=SHADOW_VERSION or
            raw.get('validation')!='SHADOW_UNVALIDATED' or
            raw.get('live_champion_changed') is not False or
            str(g.get('identity_conflict','0')) in ('1','True','true') or
            str(g.get('score_conflict','0')) in ('1','True','true')):
            continue
        # Reuse original strict pre-tip and score arithmetic checks, but
        # demand a separate shadow identifier and never treat as champion.
        test={**raw,'model':VERSION,'validation':'PASS_UNVALIDATED'}
        if not valid_frozen(test,g):continue
        prior=latest.get(raw['game_id'])
        if prior is None or dt(prior['captured_utc'])<dt(raw['captured_utc']):
            latest[raw['game_id']]=raw
    return latest



def load_json(path):
    path = Path(path)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding='utf-8'))


def valid_frozen(raw, game):
    """Only exact original matchup, cutoff and unaltered pretip capture is allowed."""
    try:
        if str(raw.get('game_id')) != str(game.get('game_id')):
            return False
        if raw.get('tip_utc') != game.get('tip_utc'):
            return False
        if raw.get('home_id') is not None and raw.get('away_id') is not None:
            if (str(raw['home_id']),str(raw['away_id'])) != (str(game['home_id']),str(game['away_id'])):
                return False
        elif raw.get('home') != game.get('home') or raw.get('away') != game.get('away'):
            return False
        if raw.get('model') not in ALLOWED_MODEL_VERSIONS:
            return False
        if not dt(raw['captured_utc']) < dt(game['tip_utc']):
            return False
        if dt(game['tip_utc']) - dt(raw['captured_utc']) > timedelta(hours=72):
            return False
        if raw.get('validation') != 'PASS_UNVALIDATED':
            return False
        if raw.get('status') not in ('PASS_LOW_SAMPLE', 'RESEARCH_ONLY'):
            return False
        margin, total = float(raw['pred_margin']), float(raw['pred_total'])
        h, a = float(raw['pred_home']), float(raw['pred_away'])
        if not all(-160 < v < 300 for v in (margin, total, h, a)):
            return False
        if abs((h-a)-margin) > .05 or abs((h+a)-total) > .05:
            return False
        return True
    except (KeyError, ValueError, TypeError, OverflowError):
        return False


def forecasts(ledger, by_id):
    path = Path(ledger)
    latest = {}
    if not path.exists():
        return latest
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            raise ValueError('Corrupt frozen ledger: cannot publish incomplete data')
        game = by_id.get(str(raw.get('game_id')))
        if not game or str(game.get('identity_conflict','0')) in ('1','True','true') or not valid_frozen(raw, game):
            continue
        key = raw['game_id']
        prev = latest.get(key)
        if prev is None or dt(prev['captured_utc']) < dt(raw['captured_utc']):
            latest[key] = raw
    return latest


def _intscore(v):
    if v in ('', None):
        return None
    return int(round(float(v)))


def export_payload(rows, ledger, now=None, historical=None, directory=None, historical_v02=None, shadow_ledger=None):
    now = now or utcnow()
    season_year = now.year + int(now.month >= 7)
    by_id = {str(g['game_id']):g for g in rows}
    frozen = forecasts(ledger, by_id)
    shadows = shadow_forecasts(shadow_ledger,by_id) if shadow_ledger is not None else {}
    directory = directory or {}
    def display(team_id, raw_name):
        return directory.get(str(team_id)) or clean_label(raw_name)
    def division(team_id):
        return 'D1' if str(team_id) in directory else ('OTHER_OR_UNVERIFIED' if directory else 'UNKNOWN')
    games = []
    team_rows = defaultdict(list)
    # Game list limited by calendar range to keep Pages small and quick to load.
    minimum = now - timedelta(days=22)
    maximum = now + timedelta(days=10)
    for g in rows:
        if int(g['season']) != season_year:
            continue
        tip = dt(g['tip_utc'])
        if not minimum <= tip <= maximum:
            continue
        completed = str(g.get('completed')) in ('1', 'True', 'true')
        hs = _intscore(g.get('home_score')) if completed else None
        ats = _intscore(g.get('away_score')) if completed else None
        original_forecast = frozen.get(str(g['game_id']))
        qualified_d1 = division(g['home_id']) == 'D1' and division(g['away_id']) == 'D1'
        disputed = str(g.get('score_conflict','0')) in ('1','True','true') or str(g.get('identity_conflict','0')) in ('1','True','true')
        # Keep original pre-tip ledger immutable; do not publish non-D-I
        # forecasts as qualified Division-I model predictions.
        pred = original_forecast if qualified_d1 and not disputed else None
        shadow = shadows.get(str(g['game_id'])) if pred is not None and qualified_d1 and not disputed else None
        rec = {'id':str(g['game_id']), 'tip_utc':g['tip_utc'],
            'home':display(g['home_id'], g['home']), 'away':display(g['away_id'],g['away']),
            'home_division':division(g['home_id']), 'away_division':division(g['away_id']),
            'home_id':str(g['home_id']),
            'away_id':str(g['away_id']), 'neutral':str(g['neutral']) in ('1','True','true'),
            'completed':completed, 'home_score':hs,'away_score':ats,
            'game_state':g.get('game_state') or ('post' if completed else 'pre'),
            'score_source_under_review':str(g.get('score_conflict','0')) in ('1','True','true'),
            'identity_source_under_review':str(g.get('identity_conflict','0')) in ('1','True','true'),
            'home_live_score':_intscore(g.get('live_home_score')) if g.get('live_home_score') not in ('',None) else None,
            'away_live_score':_intscore(g.get('live_away_score')) if g.get('live_away_score') not in ('',None) else None,
            'game_clock':g.get('game_clock') or '',
            'verified_d1_matchup':qualified_d1 and not disputed,
            'hidden_unqualified_projection':bool(original_forecast and (not qualified_d1 or disputed)),
            'projection':None, 'shadow_projection':None, 'market_spread':None,'market_total':None}
        if shadow:
            shadow_margin=float(shadow['pred_margin'])
            champion_margin=float(pred['pred_margin'])
            rec['shadow_projection']={
                'model':SHADOW_VERSION,'captured_utc':shadow['captured_utc'],
                'margin':shadow_margin,'total':float(shadow['pred_total']),
                'experimental_only':True,'never_betting_advice':True,
                'winner_disagrees':(shadow_margin>0)!=(champion_margin>0)
            }
        if pred:
            # Literal model output, not invented; pretip provenance preserved.
            rec['projection'] = {
                'model':pred['model'], 'captured_utc':pred['captured_utc'],
                'home':float(pred['pred_home']), 'away':float(pred['pred_away']),
                'margin':float(pred['pred_margin']), 'total':float(pred['pred_total']),
                'status':pred['status'], 'home_games':int(pred['home_current_games']),
                'away_games':int(pred['away_current_games']),
                'training_games':int(pred['training_games']),
                'home_win_probability_uncalibrated':float(pred['home_win_probability_uncalibrated'])}
        games.append(rec)
        if completed and hs is not None and ats is not None:
            team_rows[g['home_id']].append((hs,ats,tip,g['home']))
            team_rows[g['away_id']].append((ats,hs,tip,g['away']))
    games.sort(key=lambda v:(v['tip_utc'],v['id']))
    teams = []
    latest_names = {}
    for g in rows:
        if int(g['season']) == season_year:
            for side in ('home','away'):
                team_id = str(g[side+'_id'])
                # Only source-listed Division I teams enter the scouting list.
                # Games against other opponents still appear on the game board.
                if not directory or team_id in directory:
                    latest_names[team_id] = display(team_id, g[side])
    current_rows = defaultdict(list)
    for g in rows:
        if int(g['season']) != season_year or str(g['completed']) not in ('1','True','true'):
            continue
        if not g.get('home_score') or not g.get('away_score') or dt(g['tip_utc']) > now:
            continue
        hs, ats = float(g['home_score']),float(g['away_score'])
        current_rows[str(g['home_id'])].append((hs,ats,g['tip_utc']))
        current_rows[str(g['away_id'])].append((ats,hs,g['tip_utc']))
    for team_id, name in sorted(latest_names.items(),key=lambda v:v[1]):
        teamgames = current_rows.get(str(team_id),[])
        n = len(teamgames)
        teams.append({'id':str(team_id),'name':name,'games':n,
            'wins':sum(x[0]>x[1] for x in teamgames),
            'losses':sum(x[0]<x[1] for x in teamgames),
            'points_for':round(sum(x[0] for x in teamgames)/n,1) if n else None,
            'points_against':round(sum(x[1] for x in teamgames)/n,1) if n else None})
    completed_with_preds = [g for g in games if g['verified_d1_matchup'] and g['completed'] and g['projection'] and g['home_score'] is not None and eligible(by_id[g['id']], now, retrospective=False)]
    recent = []
    for g in completed_with_preds:
        margin = g['home_score']-g['away_score']
        predmargin = g['projection']['margin']
        recent.append({'game_id':g['id'],'tip_utc':g['tip_utc'],
            'home':g['home'],'away':g['away'],
            'winner_correct':(margin>0)==(predmargin>0),
            'actual_margin':margin,'pred_margin':predmargin,
            'actual_total':g['home_score']+g['away_score'],
            'pred_total':g['projection']['total'],
            'status':g['projection']['status']})
    # Grade the exact vetted archive records, not arbitrary ledger text. Final
    # scores must have been observed by the evaluation time.
    graded = []
    graded_d1 = []
    graded_other = []
    for game_id, pred in frozen.items():
        g = by_id[game_id]
        if not eligible(g, now, retrospective=False):
            continue
        margin = float(g['home_score']) - float(g['away_score'])
        total = float(g['home_score']) + float(g['away_score'])
        obs = {**pred,'actual_margin':margin,'actual_total':total,
               'correct':int((pred['pred_margin']>0)==(margin>0))}
        graded.append(obs)
        if division(g['home_id'])=='D1' and division(g['away_id'])=='D1':
            graded_d1.append(obs)
        else:
            graded_other.append(obs)
    # Evaluation statistics are as-of *site export time*, not a retroactive
    # adjustment to the independently frozen pre-tip score predictions.
    d1_graded_reference = metrics(graded_d1)
    for presented in games:
        frozen_projection = presented['projection']
        if frozen_projection is None:
            continue
        sample = min(int(frozen_projection['home_games']),
                     int(frozen_projection['away_games']))
        frozen_projection['reliability'] = {
            'status': 'PASS_LOW_CURRENT_SAMPLE' if sample < 5 else 'PASS_NO_VERIFIED_MARKET',
            'current_season_min_games': sample,
            'margin_mae_points': d1_graded_reference['margin_mae']
                if d1_graded_reference['n'] >= 30 else None,
            'total_mae_points': d1_graded_reference['total_mae']
                if d1_graded_reference['n'] >= 30 else None,
            'historical_comparison_games': d1_graded_reference['n'],
            'reference_as_of_utc': iso(now),
            'market_spread_verified': False,
            'ats_edge_validated': False,
            'lineup_availability_verified': False,
            'warning': 'Error averages are descriptive of previously graded D-I games; not a confidence interval or a betting edge.'
        }
    prospective = {
        'status':'PROSPECTIVE_FROZEN_RESEARCH_ONLY',
        'all_games':metrics(graded),
        'verified_d1_matchups':metrics(graded_d1),
        'other_or_unverified_matchups':metrics(graded_other),
        'established_teams':metrics([x for x in graded_d1 if x['status']=='RESEARCH_ONLY']),
        'ats_graded':0,'over_under_graded':0
    }
    return {
        'schema':1,'generated_at':iso(now), 'season':f'{season_year-1}–{str(season_year)[-2:]}',
        'model':VERSION, 'validation':'PASS_UNVALIDATED',
        'directory':{'verified_d1_teams':len(directory),
            'source':'ESPN team directory' if directory else 'UNAVAILABLE',
            'unverified_opponents_on_board':sum(g['home_division'] != 'D1' for g in games)+sum(g['away_division'] != 'D1' for g in games)},
        'source':'ESPN public scoreboard; independently stored pre-tip model ledger',
        'games':games, 'teams':teams, 'recent_frozen_results':recent,
        'performance':{'prospective':prospective,
            'retrospective_2025_26':historical,
            'retrospective_v02':historical_v02,
            'frozen_games_in_window':sum(g['projection'] is not None for g in games),
            'historical_odds_available':False},
        'notes':[
            'Only genuine pre-tip model captures appear as projected scores.',
            'ATS/O-U recommendations unavailable until timestamped market lines exist.',
            'Low-sample games are PASS, not validated betting recommendations.',
            'Model win probabilities are not calibrated.',
            'Non-Division-I and unverified opponents remain available in an optional board filter; their forecasts are not presented as verified D-I projections.',
            'No sportsbook market listing can be inferred from an ESPN schedule entry.'
        ]}


def export_site(root='data', site='site', now=None):
    root=Path(root);site=Path(site);site.mkdir(parents=True,exist_ok=True)
    rows=read_csv(root/'games.csv')
    historic=load_json(root/'reports/backtest_2026.json')
    directory=load_directory(root/'team_directory.json')
    legacy=load_json(root/'reports/backtest_2026_v02.json')
    payload=export_payload(rows,root/'forecast_ledger.jsonl',now=now,historical=historic,
        directory=directory,historical_v02=legacy,shadow_ledger=root/'shadow_ledger.jsonl')
    destination=site/'data.json'
    tmp=destination.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(payload,ensure_ascii=False,separators=(',',':'))+'\n',encoding='utf-8')
    tmp.replace(destination)
    return {'path':str(destination),'games':len(payload['games']),
        'forecasts':payload['performance']['frozen_games_in_window'],
        'frozen_graded':payload['performance']['prospective']['all_games']['n']}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',default='data')
    parser.add_argument('--site',default='site')
    args=parser.parse_args()
    print(json.dumps(export_site(args.root,args.site),indent=2))
