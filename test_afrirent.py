"""Business-logic integration tests. No real SMTP messages are sent."""
import io
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch

import pandas as pd
from pypdf import PdfReader
import Afrirent_Auto as app


class DepotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = app.Store(Path(self.tmp.name)/'test.sqlite3')
        self.db.add('a01','Tyres',3,'Tester')

    def tearDown(self):
        self.tmp.cleanup()

    def test_duplicate_case_and_spaces(self):
        with self.assertRaises(ValueError):
            self.db.add(' a01 ','More tyres',8,'Tester')
        self.assertEqual(len(self.db.rows('records')),1)

    def test_validation(self):
        for value in [-1,1.5,'NaN','infinity','two']:
            with self.assertRaises(ValueError):
                app.whole(value)
        with self.assertRaises(ValueError):
            self.db.add('   ','test',1,'Tester')
        with self.assertRaises(ValueError):
            self.db.add('a02','   ',1,'Tester')

    def test_atomic_actor_validation(self):
        with self.assertRaises(ValueError):
            self.db.add('a02','Fuel',2,'')
        self.assertEqual(len(self.db.rows('records')),1)

    def test_edit_audit_and_stale_write(self):
        self.db.change('A01','Spare tyres',4,'Tester',1)
        with self.assertRaises(ValueError):
            self.db.change('A01','Wrong',99,'Other user',1)
        self.assertEqual(self.db.rows('records')[0]['quantity'],4)
        audit=self.db.rows('audit')[-1]
        self.assertIn('Spare tyres',audit['after_json'])
        self.assertIn('Tyres',audit['before_json'])

    def test_delete_one_preserves_audit(self):
        self.db.add('A02','Oil',1,'Tester')
        self.db.change('A01','Tyres',3,'Tester',1,delete=True)
        self.assertEqual([r['id'] for r in self.db.rows('records')],['A02'])
        self.assertEqual(self.db.rows('audit')[-1]['action'],'Delete record')

    def test_reconciliation_is_snapshot(self):
        self.assertEqual(self.db.reconcile('A01',2,'Physical count','Tester',1),-1)
        self.assertEqual(self.db.rows('records')[0]['quantity'],3)
        self.db.change('A01','Tyres',2,'Tester',1)
        self.assertEqual(self.db.rows('reconciliations')[0]['expected'],3)

    def test_admin_task_and_status(self):
        self.db.task('Supplier follow-up','Request delivery date','Test supplier',datetime.now(app.TZ).isoformat(),'PO 123','Tester')
        t=self.db.rows('tasks')[0]
        self.db.task_status(t['id'],'Completed','Tester',t['version'])
        self.assertEqual(self.db.rows('tasks')[0]['status'],'Completed')
        with self.assertRaises(ValueError):
            self.db.task_status(t['id'],'Open','Tester',t['version'])

    def workbook(self, rows):
        b=io.BytesIO()
        pd.DataFrame(rows,columns=['Record ID','Item / Description','Quantity','Date Added']).to_excel(b,index=False)
        b.seek(0)
        return b

    def test_import_duplicate_is_atomic(self):
        b=self.workbook([['b01','Oil',4,'2026-09-29'],['A01','Tyres',5,'2026-09-29']])
        with self.assertRaises(ValueError):
            self.db.import_legacy(b,'Tester')
        self.assertEqual(len(self.db.rows('records')),1)

    def test_import_preserves_zero_padded_id(self):
        b=self.workbook([['0004','Oil',4,'2026-09-29 11:00']])
        self.assertEqual(self.db.import_legacy(b,'Tester'),1)
        self.assertEqual(self.db.rows('records')[1]['id'],'0004')
        self.assertIn('+02:00',self.db.rows('records')[1]['added'])

    def test_excel_export(self):
        self.db.add('B01','=1+1',2,'Tester')
        b=app.export_excel(self.db.rows('records'))
        frame=pd.read_excel(io.BytesIO(b))
        self.assertEqual(frame['Quantity'].sum(),5)
        self.assertTrue(frame.iloc[1]['Item / Description'].startswith("'="))

    def test_pdf_landscape_multipage_and_escaped_text(self):
        rows=[{'id':f'R{i:03}', 'description':'Zoë <br/> & tyre inspection '*10,'quantity':1,'added':'2026-09-29'} for i in range(80)]
        pdf=app.make_pdf(rows)
        reader=PdfReader(io.BytesIO(pdf))
        self.assertGreater(len(reader.pages),1)
        self.assertGreater(float(reader.pages[0].mediabox.width),float(reader.pages[0].mediabox.height))
        text='\n'.join(p.extract_text() for p in reader.pages)
        self.assertIn('Total quantity: 80',text)
        self.assertIn('R079',text)
        self.assertIn('Zoë',text)
        self.assertTrue(all('Record ID' in p.extract_text() for p in reader.pages))

    def test_empty_pdf(self):
        text=PdfReader(io.BytesIO(app.make_pdf([]))).pages[0].extract_text()
        self.assertIn('No depot records',text)

    @patch('Afrirent_Auto.send_smtp')
    def test_daily_due_and_once_only(self,send):
        self.db.schedule(True,'16:00','test@example.com','Tester')
        today=datetime.now(app.TZ).replace(hour=15,minute=59)
        self.assertFalse(app.run_due(self.db,{},today))
        self.assertTrue(app.run_due(self.db,{},today.replace(hour=16,minute=0)))
        self.assertFalse(app.run_due(self.db,{},today.replace(hour=17)))
        self.assertTrue(app.run_due(self.db,{},today.replace(hour=17)+timedelta(days=1)))
        self.assertEqual(send.call_count,2)

    @patch('Afrirent_Auto.send_smtp',side_effect=TimeoutError('test'))
    def test_uncertain_send_not_retried(self,send):
        with self.assertRaises(RuntimeError):
            app.dispatch(self.db,{},'test@example.com','daily:test')
        self.assertFalse(app.dispatch(self.db,{},'test@example.com','daily:test'))
        self.assertEqual(send.call_count,1)
        self.assertEqual(self.db.rows('email_runs')[0]['status'],'Failed or uncertain')

    def test_bad_recipient_rejected_before_claim(self):
        with self.assertRaises(ValueError):
            app.dispatch(self.db,{},'wrong','test')
        self.assertEqual(self.db.rows('email_runs'),[])

    @patch('Afrirent_Auto.send_smtp')
    def test_schedule_disabled(self,send):
        self.assertFalse(app.run_due(self.db,{}))
        send.assert_not_called()


if __name__=='__main__':
    unittest.main()
