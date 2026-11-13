"""Free-source NCAA men's basketball scoreboard model (research, not betting validated).

Historical replay uses final scores, but never includes a game at or after the
prediction cutoff. Retrospective capture is NOT historical availability evidence.
"""
import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from scipy.sparse import coo_matrix, diags, vstack
from scipy.sparse.linalg import lsqr
from scipy.stats import norm

from team_directory import update_directory

API = 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard'
VERSION = 'CBB_SCORE_RIDGE_V0_3'  # retain for immutable retrospective replay
ACTIVE_LIVE_VERSION = 'CBB_SCORE_RIDGE_V0_4_D1_PRIOR30_A1_5'
# Basketball venue advantage must not be inferred from a schedule full of
# stronger Division I hosts playing lower-division visiting opponents.
# A modest fixed baseline is safer until venue effects can be identified
# from balanced, strictly historical schedules.
HOME_COURT_POINTS = 3.5
FIELDS = ('game_id', 'tip_utc', 'season', 'home_id', 'away_id', 'home', 'away',
          'neutral', 'home_score', 'away_score', 'completed', 'first_final_observed_utc',
          'last_observed_utc', 'score_conflict', 'identity_conflict', 'game_state', 'live_home_score', 'live_away_score', 'game_clock')
UTC = timezone.utc


def utcnow():
    return datetime.now(UTC)


def iso(t):
    return t.astimezone(UTC).isoformat(timespec='seconds').replace('+00:00', 'Z')


def dt(s):
    return datetime.fromisoformat(s.replace('Z', '+00:00')).astimezone(UTC)


def season(t):
    return t.year + int(t.month >= 7)


def rows_from_payload(payload, observed):
    if not isinstance(payload, dict) or not isinstance(payload.get('events'), list):
        raise ValueError('Malformed ESPN response: missing events')
    seen = set()
    output = []
    for event in payload['events']:
        ident = str(event.get('id', ''))
        if not ident or ident in seen:
            raise ValueError('Duplicate or absent game ID')
        seen.add(ident)
        contests = event.get('competitions') or []
        if not contests:
            continue
        comp = contests[0]
        sides = {t.get('homeAway'): t for t in comp.get('competitors', [])}
        if set(sides) != {'home', 'away'}:
            continue
        h, a = sides['home'], sides['away']
        home, away = h.get('team', {}), a.get('team', {})
        tip = dt(event.get('date') or comp['date'])
        status = event.get('status') or comp.get('status') or {}
        status_type = status.get('type') or {}
        completed = bool(status_type.get('completed'))
        state = 'post' if completed else status_type.get('state', 'pre')
        if state not in ('pre','in','post'):
            state = 'pre'
        def score(v):
            if isinstance(v, dict):
                v = v.get('value') if v.get('value') is not None else v.get('displayValue')
            return float(v) if v not in ('', None) else None
        hs, ats = score(h.get('score')), score(a.get('score'))
        if completed and (hs is None or ats is None):
            raise ValueError('Final game missing score: ' + ident)
        if not home.get('id') or not away.get('id'):
            raise ValueError('Missing team ID: ' + ident)
        output.append(dict(game_id=ident, tip_utc=iso(tip), season=season(tip),
            home_id=str(home['id']), away_id=str(away['id']),
            home=home.get('displayName') or home.get('name') or str(home['id']),
            away=away.get('displayName') or away.get('name') or str(away['id']),
            neutral=int(bool(comp.get('neutralSite'))), home_score=hs if completed else '',
            away_score=ats if completed else '', completed=int(completed),
            first_final_observed_utc=observed if completed else '', last_observed_utc=observed,
            game_state=state, live_home_score=hs if state=='in' else '',
            live_away_score=ats if state=='in' else '', game_clock=str(status.get('displayClock') or '')))
    return output


def read_csv(path):
    p = Path(path)
    if not p.exists():
        return []
    with p.open(encoding='utf-8', newline='') as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fields=FIELDS):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    with temp.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)
    temp.replace(path)


def merge_rows(old, new):
    byid = {g['game_id']: g for g in old}
    for g in new:
        prior = byid.get(g['game_id'])
        if prior:
            if (prior['home_id'], prior['away_id'], prior['tip_utc']) != (g['home_id'], g['away_id'], g['tip_utc']):
                raise ValueError('Identity conflict: ' + g['game_id'])
            if str(prior['completed']) == '1':
                # Do not overwrite settled scores or first final observation time.
                if str(g['completed']) == '1' and (float(g['home_score']), float(g['away_score'])) != (float(prior['home_score']), float(prior['away_score'])):
                    raise ValueError('Score revision requires manual reconciliation: ' + g['game_id'])
                g.update(home_score=prior['home_score'], away_score=prior['away_score'],
                    completed=1, first_final_observed_utc=prior['first_final_observed_utc'],
                    game_state='post', live_home_score='', live_away_score='')
        if prior and str(prior.get('score_conflict', '0')) in ('1','True','true'):
            g['score_conflict'] = 1
        if prior and str(prior.get('identity_conflict', '0')) in ('1','True','true'):
            g['identity_conflict'] = 1
        byid[g['game_id']] = g
    return sorted(byid.values(), key=lambda r: (r['tip_utc'], r['game_id']))



def merge_with_score_quarantine(existing, new, observed, root, raw_sha):
    """Continue a daily sync without ever silently overwriting a revised final.

    An unresolved score conflict is a model/grade exclusion, never a
    retroactively accepted truth. Store raw conflicting evidence separately.
    Only a reviewed manual reconciliation can clear the persistent flag.
    """
    conflicts = []
    identity_conflicts = []
    by_id = {str(g['game_id']): g for g in existing}
    for g in new:
        try:
            existing = merge_rows(existing, [g])
            by_id[str(g['game_id'])] = next(r for r in existing if r['game_id'] == g['game_id'])
        except ValueError as exc:
            if str(exc).startswith('Identity conflict: '):
                prior = by_id.get(str(g['game_id']))
                if prior is None: raise
                prior['identity_conflict'] = 1
                identity_conflicts.append({
                    'game_id':str(g['game_id']),
                    'original_identity':[str(prior['home_id']),str(prior['away_id']),prior['tip_utc']],
                    'new_identity':[str(g['home_id']),str(g['away_id']),g['tip_utc']],
                    'observed_utc':observed,'source_sha256':raw_sha,
                    'resolution':'UNRESOLVED_EXCLUDED_FROM_MODEL_AND_GRADING'
                })
                continue
            if not str(exc).startswith('Score revision requires manual reconciliation: '):
                raise
            prior = by_id.get(str(g['game_id']))
            if prior is None:
                raise
            prior['score_conflict'] = 1
            conflicts.append({
                'game_id': str(g['game_id']), 'tip_utc':g['tip_utc'],
                'home_id':str(g['home_id']), 'away_id':str(g['away_id']),
                'archived_score':[float(prior['home_score']),float(prior['away_score'])],
                'new_source_score':[float(g['home_score']),float(g['away_score'])],
                'source_observed_utc':observed, 'source_sha256':raw_sha,
                'resolution':'UNRESOLVED_EXCLUDED_FROM_MODEL_AND_GRADING'
            })
    if conflicts:
        dest = Path(root)/'quarantine'/'score_revisions.json'
        dest.parent.mkdir(parents=True,exist_ok=True)
        saved = json.loads(dest.read_text(encoding='utf-8')) if dest.exists() else {}
        for event in conflicts:
            key = event['game_id']
            if key not in saved:
                saved[key] = {'first_observed_utc':observed, 'evidence':[]}
            known = {(tuple(x['archived_score']),tuple(x['new_source_score']))
                     for x in saved[key]['evidence']}
            fingerprint = (tuple(event['archived_score']),tuple(event['new_source_score']))
            if fingerprint not in known:
                saved[key]['evidence'].append(event)
            saved[key]['last_seen_utc']=observed
        temp = dest.with_suffix('.json.tmp')
        temp.write_text(json.dumps(saved,indent=2,sort_keys=True)+'\n',encoding='utf-8')
        temp.replace(dest)
    if identity_conflicts:
        dest=Path(root)/'quarantine'/'identity_conflicts.json'
        dest.parent.mkdir(parents=True,exist_ok=True)
        saved=json.loads(dest.read_text(encoding='utf-8')) if dest.exists() else {}
        for ev in identity_conflicts:
            entry=saved.setdefault(ev['game_id'],{'first_observed_utc':observed,'evidence':[]})
            pair=(ev['original_identity'],ev['new_identity'])
            if all((x['original_identity'],x['new_identity'])!=pair for x in entry['evidence']):
                entry['evidence'].append(ev)
            entry['last_seen_utc']=observed
        tmp=dest.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(saved,indent=2,sort_keys=True)+'\n',encoding='utf-8')
        tmp.replace(dest)
    return existing, len(conflicts)+len(identity_conflicts)

def fetch_day(day):
    url = API + '?' + urllib.parse.urlencode({'dates': day.strftime('%Y%m%d'), 'groups': '50', 'limit': 500})
    req = urllib.request.Request(url, headers={'User-Agent': 'cbb-model-open-research/0.2', 'Accept': 'application/json'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
            return json.loads(raw), raw
        except (OSError, ValueError):
            if attempt == 2:
                raise
            time.sleep(1 + attempt * 2)


def sync(start, end, root='data', historical=False):
    if end < start or (end-start).days > 190:
        raise ValueError('Invalid sync range: max 191 days per request')
    root = Path(root)
    path = root / 'games.csv'
    existing = read_csv(path)
    dates = 0
    obs = 0
    skipped = 0
    quarantined = 0
    market_quotes_captured = 0
    day = start
    while day <= end:
        rawdir = root / 'raw' / day.isoformat()
        if historical and list(rawdir.glob('*.json.gz')):
            skipped += 1
            day += timedelta(days=1)
            continue
        payload, raw = fetch_day(day)
        observed = iso(utcnow())
        records = rows_from_payload(payload, observed)
        # ESPN limit truncation is possible; reject saturated result explicitly.
        if len(records) >= 500:
            raise ValueError('Scoreboard potentially truncated on ' + str(day))
        rawdir.mkdir(parents=True, exist_ok=True)
        key = utcnow().strftime('%Y%m%dT%H%M%S%fZ')
        with gzip.open(rawdir / (key + '.json.gz'), 'wb') as f:
            f.write(raw)
        (rawdir / (key + '.manifest.json')).write_text(json.dumps({
            'requested_date': day.isoformat(), 'observed_utc': observed,
            'games': len(records), 'sha256': hashlib.sha256(raw).hexdigest(), 'api': API}, indent=2))
        source_sha=hashlib.sha256(raw).hexdigest()
        existing, new_conflicts = merge_with_score_quarantine(
            existing, records, observed, root, source_sha)
        quarantined += new_conflicts
        # Reuse the same freely fetched scoreboard response. Only fields the
        # source actually provides, observed BEFORE tip, may become market data.
        if not historical:
            from market_capture import append_pretip
            market_capture_result=append_pretip(payload,observed,
                root/'market'/'pretip_quotes.jsonl',source_sha)
            market_quotes_captured+=market_capture_result['captured']
        # Checkpoint before next request; interruption never silently loses all progress.
        write_csv(path, existing)
        dates += 1
        obs += len(records)
        day += timedelta(days=1)
        time.sleep(0.6)
    return {'downloaded_dates': dates, 'cached_dates': skipped, 'observed_games': obs,
            'unique_games': len(existing), 'score_revision_conflicts':quarantined,
            'market_quotes_observed_pretip':market_quotes_captured,
            'source': 'ESPN unofficial public scoreboard',
            'source_verified': bool(dates)}


def eligible(g, cutoff, retrospective):
    if str(g.get('identity_conflict','0')) in ('1','True','true'):
        return False
    if str(g.get('score_conflict', '0')) in ('1','True','true'):
        return False
    if str(g['completed']) not in ('1', 'True', 'true') or g['home_score'] == '' or g['away_score'] == '':
        return False
    if dt(g['tip_utc']) + timedelta(hours=4) > cutoff:
        return False
    return retrospective or bool(g['first_final_observed_utc'] and dt(g['first_final_observed_utc']) <= cutoff)


def fit(rows, cutoff, test_season=None, retrospective=False, alpha=3.0, prior_weight=0.60, home_court_points=HOME_COURT_POINTS, verified_ids=None, prior_team_multipliers=None):
    target = test_season or season(cutoff)
    used = []
    counts = Counter()
    verified = {str(team_id) for team_id in verified_ids} if verified_ids is not None else None
    prior_team_multipliers = (None if prior_team_multipliers is None else
                              {str(team): float(value) for team,value in
                               prior_team_multipliers.items()})
    if prior_team_multipliers is not None and any(
            not math.isfinite(value) or not 0.35 <= value <= 1.0
            for value in prior_team_multipliers.values()):
        raise ValueError("invalid roster prior multipliers")
    for g in rows:
        if int(g['season']) not in (target, target-1) or not eligible(g, cutoff, retrospective):
            continue
        if verified is not None and (str(g['home_id']) not in verified or str(g['away_id']) not in verified):
            continue
        try:
            hs, ats = float(g['home_score']), float(g['away_score'])
            if not (20 <= hs <= 200 and 20 <= ats <= 200):
                continue
            if int(g['season']) == target:
                weight = 1.0
            else:
                # Optional researched roster retention can only *reduce*
                # previous-season influence. Unverified teams retain 1.0.
                # None preserves byte-for-byte v0.4 training behavior.
                weight = prior_weight
                if prior_team_multipliers is not None:
                    hm = prior_team_multipliers.get(str(g['home_id']), 1.0)
                    am = prior_team_multipliers.get(str(g['away_id']), 1.0)
                    weight *= (hm + am) / 2.0
            used.append((g, hs, ats, weight))
            if int(g['season']) == target:
                counts[g['home_id']] += 1
                counts[g['away_id']] += 1
        except (TypeError, ValueError):
            continue
    if not used:
        raise ValueError('No eligible training games')
    teams = sorted({team for g, _, _, _ in used for team in (g['home_id'], g['away_id'])})
    index = {team: i for i, team in enumerate(teams)}
    n = len(index)
    I, J, V, y = [], [], [], []
    for g, hs, ats, weight in used:
        h, a = index[g['home_id']], index[g['away_id']]
        non_neutral = str(g['neutral']) not in ('1', 'True', 'true')
        for score, team_o, team_d, sign in ((hs,h,a,1),(ats,a,h,-1)):
            r = len(y)
            scale = math.sqrt(weight)
            # The venue feature is fixed rather than freely estimated. A free
            # home term was severely confounded by guarantee-game schedules.
            for col, val in ((0,1), (2+team_o,1), (2+n+team_d,-1)):
                if val:
                    I.append(r); J.append(col); V.append(scale*val)
            y.append(scale*(score - sign*int(non_neutral)*home_court_points/2))
    X = coo_matrix((V, (I,J)), shape=(len(y), 2+2*n)).tocsr()
    reg = np.array([1e-7, 1.0] + [alpha]*(2*n))
    augmented = vstack([X,diags(np.sqrt(reg))],format='csr')
    beta = lsqr(augmented, np.r_[y, np.zeros(2+2*n)], atol=1e-7, btol=1e-7, iter_lim=800)[0]
    residuals = np.asarray(y) - X @ beta
    beta[1] = float(home_court_points)
    pair = residuals.reshape(-1,2)
    margin_sd = max(8.0, float(np.std(pair[:,0]-pair[:,1])))
    return {'beta': beta, 'idx': index, 'counts': counts, 'n_games': len(used),
            'margin_sd': margin_sd, 'cutoff': iso(cutoff), 'season': target,
            'training_latest_tip': max(g['tip_utc'] for g, _, _, _ in used),
            'home_court_points': float(home_court_points),
            'previous_season_weight': prior_weight, 'ridge_alpha': alpha,
            'verified_d1_training_only':verified is not None,
            'roster_adjusted_prior':prior_team_multipliers is not None}


def predict(model, g, min_current=5):
    ix, b = model['idx'], model['beta']
    h, a = g['home_id'], g['away_id']
    if h not in ix or a not in ix:
        return None
    n = len(ix)
    hca = 0 if str(g['neutral']) in ('1', 'True', 'true') else b[1]
    hs = float(b[0]+hca/2+b[2+ix[h]]-b[2+n+ix[a]])
    ats = float(b[0]-hca/2+b[2+ix[a]]-b[2+n+ix[h]])
    margin = hs-ats
    hc, ac = model['counts'][h], model['counts'][a]
    return {'game_id': g['game_id'], 'home': g['home'], 'away': g['away'],
            'home_id':str(h), 'away_id':str(a),
            'tip_utc':g['tip_utc'], 'model': VERSION, 'neutral':str(g['neutral']) == '1',
            'home_current_games': hc, 'away_current_games': ac,
            'status':'RESEARCH_ONLY' if min(hc,ac)>=min_current else 'PASS_LOW_SAMPLE',
            'pred_home':round(hs,2), 'pred_away':round(ats,2),
            'pred_margin':round(margin,2), 'pred_total':round(hs+ats,2),
            'home_win_probability_uncalibrated':round(float(norm.cdf(margin/model['margin_sd'])),4),
            'training_games':model['n_games'], 'training_latest_tip':model['training_latest_tip'],
            'home_court_points':model['home_court_points']}


def metrics(preds):
    n = len(preds)
    if not n:
        return {'n':0,'su_accuracy':None,'margin_mae':None,'total_mae':None,'margin_bias':None}
    return {'n':n,'su_accuracy':round(sum(p['correct'] for p in preds)/n,4),
        'margin_mae':round(sum(abs(p['actual_margin']-p['pred_margin']) for p in preds)/n,3),
        'total_mae':round(sum(abs(p['actual_total']-p['pred_total']) for p in preds)/n,3),
        'margin_bias':round(sum(p['pred_margin']-p['actual_margin'] for p in preds)/n,3)}


def backtest(rows, test_season=2026, minimum_games=5):
    """Day-by-day historical score-only replay; source acquisition is retrospective."""
    finals = [g for g in rows if int(g['season']) == test_season and str(g['completed'])=='1'
              and g['home_score']!='' and g['away_score']!=''
              and str(g.get('score_conflict','0')) not in ('1','True','true')
              and str(g.get('identity_conflict','0')) not in ('1','True','true')]
    groups = defaultdict(list)
    for g in finals:
        groups[g['tip_utc'][:10]].append(g)
    predictions = []
    for day in sorted(groups):
        cutoff = dt(day+'T00:00:00Z')
        try:
            model = fit(rows, cutoff, test_season=test_season, retrospective=True)
        except ValueError:
            continue
        for g in groups[day]:
            p = predict(model,g,min_current=minimum_games)
            if p is None:
                continue
            m=float(g['home_score'])-float(g['away_score'])
            t=float(g['home_score'])+float(g['away_score'])
            p.update(actual_margin=m,actual_total=t,correct=int((p['pred_margin']>0)==(m>0)),
                     cold_start=p['status']=='PASS_LOW_SAMPLE', cutoff_utc=iso(cutoff))
            # Never include same-day or future finished games in a training fold.
            if dt(p['training_latest_tip']) >= cutoff:
                raise AssertionError('Backtest leakage detected')
            predictions.append(p)
    core=[p for p in predictions if not p['cold_start']]
    home_baseline=[]
    for p in predictions:
        p0 = {'pred_margin':0 if p['neutral'] else 3.0, 'pred_total':145.0,
              'actual_margin':p['actual_margin'],'actual_total':p['actual_total'],
              'correct':int(((0 if p['neutral'] else 3.0)>0)==(p['actual_margin']>0))}
        home_baseline.append(p0)
    return {'report':{'status':'RETROSPECTIVE_WALK_FORWARD_RESEARCH_ONLY',
        'model':VERSION,'test_season':test_season,'games_with_final_scores':len(finals),
        'all_games':metrics(predictions),'established_teams':metrics(core),
        'cold_start':metrics([p for p in predictions if p['cold_start']]),
        'simple_baseline':metrics(home_baseline),
        'baseline_definition':'Home by 3 (neutral tie), total 145; intentionally naive not optimized',
        'home_court_points': HOME_COURT_POINTS, 'ridge_alpha':3.0, 'previous_season_weight':0.60,
        'historical_odds_available':False,'ats_graded':0,'over_under_graded':0,
        'caveat':'Scores retrospectively acquired; point-in-time score-only replay, not verified pregame source snapshots.'},
        'predictions':predictions}


def load_verified_d1_ids(path):
    from team_directory import load_directory
    try:
        names=load_directory(path)
    except (OSError,ValueError):
        names={}
    return names.keys()  # Empty directory => do not infer membership.

def append_pregame(rows, ledger, now=None, verified_ids=None):
    now=now or utcnow()
    verified={str(t) for t in verified_ids} if verified_ids is not None else None
    if verified is not None and not verified:
        return {'status':'PASS_NO_VERIFIED_D1_DIRECTORY','captured':0}
    # v0.4 applies only to newly captured, genuinely pretip games.
    # The v0.3 retrospective code and all prior frozen ledgers stay intact.
    try:
        model=fit(rows,now,retrospective=False,alpha=1.5,
                  prior_weight=0.30,verified_ids=verified)
    except ValueError:
        return {'status':'PASS_NO_OBSERVED_HISTORY','captured':0}
    existing=[]
    p=Path(ledger)
    if p.exists():
        existing=[json.loads(s) for s in p.read_text().splitlines() if s.strip()]
    captured=[]
    seen={(x['game_id'],x['captured_utc'],x['model']) for x in existing}
    for g in rows:
        tip=dt(g['tip_utc'])
        if str(g.get('score_conflict','0')) in ('1','True','true') or str(g.get('identity_conflict','0')) in ('1','True','true'):
            continue
        if verified is not None and (str(g['home_id']) not in verified or str(g['away_id']) not in verified):
            continue
        if not now < tip <= now + timedelta(hours=72):
            continue
        result=predict(model,g)
        if result:
            result.update(model=ACTIVE_LIVE_VERSION, captured_utc=iso(now),
                          validation='PASS_UNVALIDATED',market_odds=None,
                          model_alpha=1.5,prior_season_game_weight=0.30,
                          verified_d1_training=verified is not None)
            key=(result['game_id'],result['captured_utc'],result['model'])
            if key not in seen:
                captured.append(result)
    p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('a',encoding='utf-8') as f:
        for prediction in captured:
            f.write(json.dumps(prediction,sort_keys=True)+'\n')
    return {'status':'CAPTURED_RESEARCH_ONLY','model':ACTIVE_LIVE_VERSION,
            'captured':len(captured),
            'current_season_training_games':sum(model['counts'].values())//2,
            'home_court_points':model['home_court_points']}


def grade(rows, ledger, now=None):
    now=now or utcnow()
    by_id={g['game_id']:g for g in rows}
    last={}
    p=Path(ledger)
    for line in (p.read_text().splitlines() if p.exists() else []):
        rec=json.loads(line)
        g=by_id.get(rec['game_id'])
        if not g or not eligible(g,now,retrospective=False):
            continue
        if rec['tip_utc'] != g['tip_utc'] or not dt(rec['captured_utc'])<dt(g['tip_utc']):
            continue
        # Version 0.3 freezes stable team IDs; legacy 0.2 freezes raw names.
        if rec.get('home_id') is not None and rec.get('away_id') is not None:
            if (str(rec['home_id']),str(rec['away_id'])) != (str(g['home_id']),str(g['away_id'])):
                continue
        elif rec['home'] != g['home'] or rec['away'] != g['away']:
            continue
        prior=last.get(rec['game_id'])
        if prior is None or prior['captured_utc']<rec['captured_utc']:
            last[rec['game_id']]=rec
    scores=[]
    for gid,pred in last.items():
        g=by_id[gid]
        m=float(g['home_score'])-float(g['away_score'])
        total=float(g['home_score'])+float(g['away_score'])
        scores.append({**pred,'actual_margin':m,'actual_total':total,
                       'correct':int((pred['pred_margin']>0)==(m>0))})
    by_model=defaultdict(list)
    for obs in scores:
        by_model[obs.get('model','UNKNOWN')].append(obs)
    return {'status':'PROSPECTIVE_FROZEN_RESEARCH_ONLY', 'all_games':metrics(scores),
            'by_model':{version:metrics(v) for version,v in by_model.items()},
            'established_teams':metrics([s for s in scores if s['status']=='RESEARCH_ONLY']),
            'ats_graded':0,'over_under_graded':0}


def report_file(path,obj):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(obj,indent=2,sort_keys=True)+'\n',encoding='utf-8')


def main():
    ap=argparse.ArgumentParser()
    sub=ap.add_subparsers(dest='command',required=True)
    for cmd in ('bootstrap','daily','backtest'):
        p=sub.add_parser(cmd)
        p.add_argument('--root',default='data')
        if cmd=='backtest':
            p.add_argument('--season',type=int,default=2026)
    args=ap.parse_args()
    root=Path(args.root)
    if args.command=='bootstrap':
        report=[]
        # Keep each segment below the safe 191-day maximum.
        for start,end in (('2024-11-01','2025-04-10'),('2025-11-01','2026-04-10'),
                          ('2026-11-01',min(date.today()+timedelta(days=3),date(2027,4,10)).isoformat())):
            if date.fromisoformat(start)>date.fromisoformat(end):
                continue
            report.append(sync(date.fromisoformat(start),date.fromisoformat(end),root,historical=True))
        try:
            directory = update_directory(root/'team_directory.json', iso(utcnow()))
            print('Verified ESPN Division I directory:', directory['team_count'])
        except (OSError, ValueError, KeyError) as exc:
            print('WARNING: team directory unavailable; no new Division I status will be inferred:', exc)
        rows=read_csv(root/'games.csv')
        result=backtest(rows,2026)
        report_file(root/'reports/backtest_2026.json',result['report'])
        write_csv(root/'reports/backtest_2026_games.csv',result['predictions'],
                  ('game_id','tip_utc','home','away','cutoff_utc','training_games','training_latest_tip',
                   'status','pred_margin','actual_margin','pred_total','actual_total','correct'))
        print(json.dumps({'sync':report,'backtest':result['report']},indent=2))
    elif args.command=='daily':
        today=date.today()
        ingested=sync(today-timedelta(days=7),today+timedelta(days=3),root)
        try:
            directory = update_directory(root/'team_directory.json', iso(utcnow()))
            print('Verified ESPN Division I directory:', directory['team_count'])
        except (OSError, ValueError, KeyError) as exc:
            print('WARNING: team directory unavailable; retaining only an earlier verified directory:', exc)
        rows=read_csv(root/'games.csv')
        result={'sync':ingested, 'forecast':append_pregame(rows,root/'forecast_ledger.jsonl',verified_ids=load_verified_d1_ids(root/'team_directory.json')),
                'prospective':grade(rows,root/'forecast_ledger.jsonl')}
        report_file(root/'reports/prospective.json',result['prospective'])
        print(json.dumps(result,indent=2))
    else:
        result=backtest(read_csv(root/'games.csv'),args.season)
        report_file(root/f'reports/backtest_{args.season}.json',result['report'])
        write_csv(root/f'reports/backtest_{args.season}_games.csv',result['predictions'],
                  ('game_id','tip_utc','home','away','cutoff_utc','training_games','training_latest_tip',
                   'status','pred_margin','actual_margin','pred_total','actual_total','correct'))
        print(json.dumps(result['report'],indent=2))


if __name__=='__main__':
    main()
