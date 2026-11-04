"""Free ESPN Division-I team directory, keyed by stable provider IDs.

The scoreboard deliberately includes non-Division-I opponents. Never infer a
team's division, nor silently invent/rename an opponent based on string matches.
"""
import hashlib
import json
import re
import urllib.request
from pathlib import Path

TEAM_API = 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/teams?limit=500'
MIN_D1_TEAMS = 300
MAX_D1_TEAMS = 425


def clean_label(value):
    name = ' '.join(str(value or '').split()).strip()
    # Some source names contain the institution twice, once in all caps:
    # "Regis University REGIS UNIVERSITY". Drop only an exact duplicated tail.
    parts = name.split()
    for mid in range(1, len(parts)):
        lhs, rhs = ' '.join(parts[:mid]), ' '.join(parts[mid:])
        if lhs.casefold() == rhs.casefold():
            return lhs
    return name


def parse_directory(payload):
    entries = []
    for sport in payload.get('sports', []):
        for league in sport.get('leagues', []):
            entries.extend(league.get('teams', []))
    found = {}
    for item in entries:
        team = item.get('team', item) if isinstance(item, dict) else {}
        ident = str(team.get('id', '')).strip()
        raw_name = team.get('displayName') or ' '.join([team.get('location') or '', team.get('name') or '']).strip()
        name = clean_label(raw_name)
        if not ident or not name:
            continue
        if ident in found and found[ident] != name:
            raise ValueError('Team identity conflict for ESPN ID ' + ident)
        found[ident] = name
    if not MIN_D1_TEAMS <= len(found) <= MAX_D1_TEAMS:
        raise ValueError(f'ESPN team directory coverage outside expected Division I range: {len(found)}')
    return found


def update_directory(path, observed_utc, opener=None):
    """Record source bytes and timestamp; don't replace valid cache on failure."""
    opener = opener or urllib.request.urlopen
    request = urllib.request.Request(TEAM_API, headers={'User-Agent':'cbb-free-research/0.3','Accept':'application/json'})
    with opener(request, timeout=25) as response:
        raw = response.read()
    names = parse_directory(json.loads(raw))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {'source':TEAM_API,'observed_utc':observed_utc,
           'sha256':hashlib.sha256(raw).hexdigest(), 'team_count':len(names), 'teams':names}
    tmp=path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(doc,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    tmp.replace(path)
    return doc


def load_directory(path):
    p=Path(path)
    if not p.is_file():
        return {}
    doc=json.loads(p.read_text(encoding='utf-8'))
    if not isinstance(doc.get('teams'),dict):
        raise ValueError('Corrupt team directory cache')
    entries={str(k):clean_label(v) for k,v in doc['teams'].items()}
    if not MIN_D1_TEAMS <= len(entries) <= MAX_D1_TEAMS:
        raise ValueError('Unsafe Division I directory coverage')
    return entries
