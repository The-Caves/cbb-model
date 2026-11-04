import io
import json
import pytest
from team_directory import clean_label, parse_directory, update_directory, load_directory


def fake_payload(count=355):
    return {'sports':[{'leagues':[{'teams':[{'team':{'id':str(i),'displayName':f'College {i} Bears'}}
        for i in range(1,count+1)]}]}]}


def test_clean_exact_repeated_suffix_only():
    assert clean_label('Regis University REGIS UNIVERSITY') == 'Regis University'
    assert clean_label('South Carolina Gamecocks') == 'South Carolina Gamecocks'
    assert clean_label('West Coast Baptist Eagles') == 'West Coast Baptist Eagles'


def test_directory_must_be_realistic_and_unique():
    assert len(parse_directory(fake_payload()))==355
    with pytest.raises(ValueError,match='coverage'):
        parse_directory(fake_payload(10))
    malformed=fake_payload()
    malformed['sports'][0]['leagues'][0]['teams'].append({'team':{'id':'12','displayName':'Wrong Identity'}})
    with pytest.raises(ValueError,match='conflict'):
        parse_directory(malformed)


def test_source_snapshot_persisted_only_if_complete(tmp_path):
    path=tmp_path/'directory.json'
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def read(self):return json.dumps(fake_payload()).encode()
    doc=update_directory(path,'2026-11-03T20:00:00Z',opener=lambda *args,**kwargs:Response())
    assert doc['team_count']==355
    assert load_directory(path)['15']=='College 15 Bears'
    with pytest.raises(ValueError):
        update_directory(path,'2026-11-03T21:00:00Z',opener=lambda *args,**kwargs:BadResponse())
    assert load_directory(path)['15']=='College 15 Bears'

class BadResponse:
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def read(self):return json.dumps(fake_payload(2)).encode()
