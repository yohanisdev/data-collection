import csv
import pytest
from backend import db
from backend.importer import import_csv
from backend.models import Sentence

def test_repeatable_unicode_csv(app,tmp_path):
    path=tmp_path/'source.csv'
    with path.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['english_text']);w.writeheader();w.writerow({'english_text':'“Hello,” said A.'});w.writerow({'english_text':''})
    with app.app_context():
        assert import_csv(path)=={'imported':1,'skipped':1,'invalid':0,'duplicates':0}
        assert import_csv(path)['duplicates']==1
        assert Sentence.query.first().english_text=='“Hello,” said A.'

def test_bad_column_reports_usefully(app,tmp_path):
    path=tmp_path/'bad.csv';path.write_text('wrong\nline\n',encoding='utf-8')
    with app.app_context(),pytest.raises(ValueError,match='english_text'):
        import_csv(path)
