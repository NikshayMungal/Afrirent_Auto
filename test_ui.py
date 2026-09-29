"""Headless Streamlit workflow smoke test; temporary database, no email sending."""
import tempfile
from pathlib import Path
from streamlit.testing.v1 import AppTest
from Afrirent_Auto import ROOT, Store


def widget(at, kind, label):
    return next(w for w in getattr(at, kind) if w.label == label)


if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as directory:
        dbpath = str(Path(directory)/'ui.sqlite3')
        at = AppTest.from_file(str(ROOT/'Afrirent_Auto.py'), default_timeout=20)
        at.secrets['AFRIRENT_DB'] = dbpath
        at.secrets['APP_PASSWORD'] = ''
        at.run()
        assert not at.exception, at.exception
        widget(at,'text_input','Your name (for audit history)').set_value('UI Tester')
        widget(at,'text_input','Record ID / item code').set_value('UI001')
        widget(at,'text_area','Item description / category').set_value('Test oil')
        widget(at,'button','Save record').click().run()
        assert not at.exception, at.exception
        db=Store(dbpath)
        assert len(db.rows('records'))==1
        widget(at,'number_input','Updated quantity').set_value(5)
        widget(at,'button','Save changes').click().run()
        assert not at.exception, at.exception
        assert db.rows('records')[0]['quantity']==5
        widget(at,'text_input','Search ID or description').set_value('[').run()
        assert not at.exception, at.exception
        widget(at,'text_input','Search ID or description').set_value('').run()
        widget(at,'text_area','Evidence / reason for variance').set_value('Counted 4')
        widget(at,'number_input','Physical / verified quantity').set_value(4)
        widget(at,'button','Save reconciliation').click().run()
        assert not at.exception, at.exception
        assert db.rows('reconciliations')[0]['variance']==-1
        widget(at,'text_input','Subject / task').set_value('Request delivery date')
        widget(at,'button','Add administration task').click().run()
        assert not at.exception, at.exception
        assert len(db.rows('tasks'))==1
        widget(at,'selectbox','Status').set_value('Completed')
        widget(at,'button','Update status').click().run()
        assert not at.exception, at.exception
        assert db.rows('tasks')[0]['status']=='Completed'
        widget(at,'button','Generate PDF').click().run()
        assert not at.exception, at.exception
        assert at.session_state['pdf'].startswith(b'%PDF')
        widget(at,'button','Save schedule').click().run()
        assert not at.exception, at.exception
        assert db.setting('schedule')['enabled'] is False
        widget(at,'text_input','Type the exact Record ID to confirm deletion').set_value('WRONG')
        widget(at,'button','Delete record').click().run()
        assert len(db.rows('records'))==1
        widget(at,'text_input','Type the exact Record ID to confirm deletion').set_value('UI001')
        widget(at,'button','Delete record').click().run()
        assert not at.exception, at.exception
        assert db.rows('records')==[]
        print('Streamlit workflow smoke test passed: capture, edit, literal search, reconciliation, tasks, PDF, schedule and confirmed deletion.')
