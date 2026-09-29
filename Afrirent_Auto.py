"""Afrirent depot administration. Run: streamlit run Afrirent_Auto.py"""
from __future__ import annotations

import io
import json
import os
import re
import sqlite3
import ssl
import smtplib
from contextlib import contextmanager
from datetime import datetime, timezone, date, time
from pathlib import Path
from email.message import EmailMessage
from uuid import uuid4
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, LongTable, TableStyle

ROOT = Path(__file__).resolve().parent
TZ = ZoneInfo('Africa/Johannesburg')
KINDS = ['Correspondence', 'Appointment', 'Supplier follow-up', 'General task']
STATUSES = ['Open', 'In progress', 'Completed', 'Cancelled']


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def required(value, label, limit=200):
    value = str(value).strip()
    if not value or len(value) > limit:
        raise ValueError(f'{label} must contain 1–{limit} characters.')
    return value


def whole(value):
    try:
        n = float(value)
        if not n.is_integer() or not 0 <= n <= 1_000_000_000:
            raise ValueError
        return int(n)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('Quantity must be a whole number from 0 to 1,000,000,000.')


def addresses(value):
    parts = [x.strip() for x in str(value).split(',')]
    if not parts or any(not re.fullmatch(r'[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+', x) for x in parts):
        raise ValueError('Enter valid email addresses separated by commas.')
    return list(dict.fromkeys(parts))


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.tx() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS records (
              id TEXT PRIMARY KEY COLLATE NOCASE, description TEXT NOT NULL,
              quantity INTEGER NOT NULL CHECK(quantity>=0), added TEXT NOT NULL,
              updated TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS audit (
              seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, actor TEXT,
              action TEXT, entity TEXT, before_json TEXT, after_json TEXT);
            CREATE TABLE IF NOT EXISTS tasks (
              id TEXT PRIMARY KEY, kind TEXT, title TEXT, contact TEXT,
              due TEXT, status TEXT, details TEXT, version INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS reconciliations (
              id TEXT PRIMARY KEY, record_id TEXT, expected INTEGER, actual INTEGER,
              variance INTEGER, note TEXT, actor TEXT, at TEXT);
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS email_runs (
              id TEXT PRIMARY KEY, at TEXT, recipient TEXT, status TEXT, detail TEXT);
            ''')

    @contextmanager
    def tx(self):
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        try:
            c.execute('BEGIN IMMEDIATE')
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def rows(self, table):
        if table not in {'records', 'audit', 'tasks', 'reconciliations', 'email_runs'}:
            raise ValueError('Unknown table.')
        with self.tx() as c:
            return [dict(r) for r in c.execute(f'SELECT * FROM {table}')]

    def log(self, c, actor, action, entity, before=None, after=None):
        actor = required(actor, 'Operator name', 100)
        c.execute('INSERT INTO audit(at,actor,action,entity,before_json,after_json) VALUES(?,?,?,?,?,?)',
                  (now(), actor, action, entity, json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False)))

    def add(self, rid, description, quantity, actor, added=None):
        rid = required(rid, 'Record ID', 80).upper()
        description = required(description, 'Description', 1000)
        quantity = whole(quantity)
        stamp = now()
        with self.tx() as c:
            try:
                c.execute('INSERT INTO records VALUES(?,?,?,?,?,1)', (rid, description, quantity, added or stamp, stamp))
            except sqlite3.IntegrityError:
                raise ValueError('That Record ID already exists. Use Edit instead.')
            self.log(c, actor, 'Create record', rid, after={'description': description, 'quantity': quantity})

    def change(self, rid, description, quantity, actor, version, delete=False):
        description = required(description, 'Description', 1000)
        quantity = whole(quantity)
        with self.tx() as c:
            row = c.execute('SELECT * FROM records WHERE id=?', (rid,)).fetchone()
            if row is None or row['version'] != version:
                raise ValueError('Record changed or was deleted by another user. Refresh and retry.')
            before = dict(row)
            if delete:
                c.execute('DELETE FROM records WHERE id=?', (rid,))
                after = None
            else:
                c.execute('UPDATE records SET description=?,quantity=?,updated=?,version=version+1 WHERE id=?',
                          (description, quantity, now(), rid))
                after = dict(c.execute('SELECT * FROM records WHERE id=?', (rid,)).fetchone())
            self.log(c, actor, 'Delete record' if delete else 'Edit record', rid, before, after)

    def reconcile(self, rid, actual, note, actor, version):
        actual = whole(actual)
        note = required(note, 'Reconciliation note', 1000)
        with self.tx() as c:
            row = c.execute('SELECT * FROM records WHERE id=?', (rid,)).fetchone()
            if row is None or row['version'] != version:
                raise ValueError('Record changed. Refresh before reconciling.')
            result = {'record_id': rid, 'expected': row['quantity'], 'actual': actual,
                      'variance': actual-row['quantity'], 'note': note}
            c.execute('INSERT INTO reconciliations VALUES(?,?,?,?,?,?,?,?)',
                      (uuid4().hex, rid, row['quantity'], actual, result['variance'], note, actor, now()))
            self.log(c, actor, 'Reconcile', rid, after=result)
        return result['variance']

    def task(self, kind, title, contact, due, details, actor):
        if kind not in KINDS:
            raise ValueError('Unknown task type.')
        title = required(title, 'Subject / task', 200)
        parsed = datetime.fromisoformat(due)
        if parsed.tzinfo is None:
            raise ValueError('Due time must include a timezone.')
        due = parsed.astimezone(TZ).isoformat(timespec='seconds')
        if len(contact) > 200 or len(details) > 5000:
            raise ValueError('Contact or details too long.')
        tid = uuid4().hex
        with self.tx() as c:
            c.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,1)', (tid, kind, title, contact.strip(), due, 'Open', details.strip()))
            self.log(c, actor, 'Create admin task', tid, after={'kind': kind, 'title': title, 'due': due})

    def task_status(self, tid, status, actor, version):
        if status not in STATUSES:
            raise ValueError('Unknown status.')
        with self.tx() as c:
            old = c.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()
            if old is None or old['version'] != version:
                raise ValueError('Task changed. Refresh and retry.')
            c.execute('UPDATE tasks SET status=?,version=version+1 WHERE id=?', (status, tid))
            self.log(c, actor, 'Update task status', tid, dict(old), {'status': status})

    def setting(self, key, default=None):
        with self.tx() as c:
            row = c.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def schedule(self, enabled, hhmm, recipient, actor):
        time.fromisoformat(hhmm)
        if enabled:
            addresses(recipient)
        value = {'enabled': bool(enabled), 'time': hhmm, 'recipient': recipient.strip(),
                 'start_date': datetime.now(TZ).date().isoformat()}
        with self.tx() as c:
            old = c.execute("SELECT value FROM settings WHERE key='schedule'").fetchone()
            c.execute("INSERT OR REPLACE INTO settings VALUES('schedule',?)", (json.dumps(value),))
            self.log(c, actor, 'Set daily report schedule', 'schedule', json.loads(old[0]) if old else None, value)

    def import_legacy(self, source, actor):
        frame = pd.read_excel(source, dtype={'Record ID': str}, keep_default_na=False)
        columns = ['Record ID', 'Item / Description', 'Quantity', 'Date Added']
        if not set(columns).issubset(frame.columns):
            raise ValueError('Excel must contain: ' + ', '.join(columns))
        prepared, seen = [], set()
        for n, row in enumerate(frame.to_dict('records'), 2):
            rid = required(row['Record ID'], f'Row {n} ID', 80).upper()
            if rid in seen:
                raise ValueError(f'Duplicate ID {rid} at Excel row {n}. No rows imported.')
            seen.add(rid)
            desc = required(row['Item / Description'], f'Row {n} description', 1000)
            stamp = pd.to_datetime(row['Date Added'], errors='raise')
            if pd.isna(stamp):
                raise ValueError(f'Missing date at Excel row {n}.')
            if stamp.tzinfo is None:
                stamp = stamp.tz_localize(TZ)
            prepared.append((rid, desc, whole(row['Quantity']), stamp.isoformat(), now(), 1))
        with self.tx() as c:
            try:
                c.executemany('INSERT INTO records VALUES(?,?,?,?,?,?)', prepared)
            except sqlite3.IntegrityError:
                raise ValueError('An imported ID already exists. No rows imported.')
            self.log(c, actor, 'Import Excel', 'records', after={'count': len(prepared)})
        return len(prepared)


def export_excel(records):
    columns = ['Record ID', 'Item / Description', 'Quantity', 'Date Added']
    def safe(v):
        return "'"+v if isinstance(v, str) and v.startswith(('=', '+', '-', '@')) else v
    data = [[safe(r['id']), safe(r['description']), r['quantity'], r['added']] for r in records]
    b = io.BytesIO()
    pd.DataFrame(data, columns=columns).to_excel(b, index=False, engine='openpyxl')
    return b.getvalue()


def make_pdf(records, tasks=(), scope='All depot records'):
    b = io.BytesIO()
    styles = getSampleStyleSheet()
    # Use bundled Unicode font when available; basic Helvetica otherwise.
    font_path = ROOT / 'DejaVuSans.ttf'
    if font_path.exists():
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        if 'DepotUnicode' not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont('DepotUnicode', str(font_path)))
        for name in ['Normal', 'BodyText', 'Title', 'Heading2']:
            styles[name].fontName = 'DepotUnicode'
    styles['BodyText'].fontSize = 8
    styles['BodyText'].leading = 11
    def p(value):
        return Paragraph(escape(str(value)).replace('\n', '<br/>'), styles['BodyText'])
    elements = [Paragraph('Afrirent Depot Activity Report', styles['Title']),
                p('Generated: '+datetime.now(TZ).strftime('%Y-%m-%d %H:%M SAST')),
                p('Scope: '+scope), p(f'Records: {len(records)} | Total quantity: {sum(r["quantity"] for r in records)}'),
                p('Quantity total is meaningful only when records use compatible units.'), Spacer(1, 12)]
    def table(headers, rows, widths):
        t = LongTable([[p(x) for x in headers]]+[[p(x) for x in row] for row in rows], colWidths=widths, repeatRows=1)
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#DCE6F1')),
                              ('VALIGN',(0,0),(-1,-1),'TOP'), ('GRID',(0,0),(-1,-1),0.3,colors.lightgrey),
                              ('TOPPADDING',(0,0),(-1,-1),6), ('BOTTOMPADDING',(0,0),(-1,-1),6)]))
        return t
    elements.append(table(['Record ID', 'Description', 'Quantity', 'Date added'],
                          [[r['id'], r['description'], r['quantity'], r['added']] for r in records], [115,365,70,220]))
    active = [t for t in tasks if t['status'] not in ('Completed', 'Cancelled')]
    if active:
        elements.extend([Spacer(1,16),Paragraph('Open administration and follow-ups',styles['Heading2'])])
        elements.append(table(['Type', 'Subject', 'Contact', 'Due (SAST)', 'Status'],
                              [[t['kind'], t['title'], t['contact'], t['due'], t['status']] for t in active], [110,245,145,170,100]))
    if not records:
        elements.append(p('No depot records in this selection.'))
    SimpleDocTemplate(b, pagesize=landscape(A4), leftMargin=35,rightMargin=35,topMargin=30,bottomMargin=30).build(elements)
    return b.getvalue()


def send_smtp(config, recipient, pdf):
    recipients = addresses(recipient)
    sender = addresses(config.get('SMTP_FROM', config.get('SMTP_USER', '')))
    if len(sender) != 1:
        raise ValueError('Configure exactly one sender.')
    for key in ['SMTP_HOST','SMTP_USER','SMTP_PASSWORD']:
        if not config.get(key):
            raise ValueError(f'Missing {key}; configure email settings first.')
    mode = str(config.get('SMTP_SECURITY','starttls')).lower()
    if mode not in ('ssl','starttls'):
        raise ValueError('SMTP_SECURITY must be ssl or starttls.')
    port = int(config.get('SMTP_PORT',465 if mode == 'ssl' else 587))
    msg = EmailMessage()
    msg['From'], msg['To'] = sender[0], ', '.join(recipients)
    msg['Subject'] = 'Afrirent Depot Report — '+datetime.now(TZ).date().isoformat()
    msg.set_content('Dear Management,\n\nAttached is the depot summary, including open administration tasks.\n\nKind regards,\nDepot Administration')
    msg.add_attachment(pdf, maintype='application', subtype='pdf', filename='Afrirent_Depot_Report.pdf')
    context = ssl.create_default_context()
    if mode == 'ssl':
        server = smtplib.SMTP_SSL(config['SMTP_HOST'],port,timeout=30,context=context)
    else:
        server = smtplib.SMTP(config['SMTP_HOST'],port,timeout=30)
    try:
        if mode == 'starttls':
            server.starttls(context=context)
        server.login(config['SMTP_USER'], config['SMTP_PASSWORD'])
        refused = server.send_message(msg)
        if refused:
            raise RuntimeError('Some recipients refused; verify delivery before retrying.')
    finally:
        server.close()


def dispatch(store, config, recipient, run_id, pdf=None, actor='Scheduler'):
    addresses(recipient)
    with store.tx() as c:
        try:
            c.execute('INSERT INTO email_runs VALUES(?,?,?,?,?)', (run_id, now(), recipient, 'Attempting', 'Check delivery before retrying if interrupted.'))
        except sqlite3.IntegrityError:
            return False
        store.log(c, actor, 'Attempt report email', run_id, after={'recipient':recipient})
    try:
        payload = pdf if pdf is not None else make_pdf(store.rows('records'), store.rows('tasks'))
        send_smtp(config, recipient, payload)
    except Exception as exc:
        # SMTP acceptance can be uncertain on timeout; do not retry automatically.
        with store.tx() as c:
            c.execute('UPDATE email_runs SET status=?,detail=? WHERE id=?',
                      ('Failed or uncertain', type(exc).__name__+': verify settings and delivery before retrying.',run_id))
        raise RuntimeError('Email failed or delivery is uncertain. Check email history/settings before retrying.') from None
    with store.tx() as c:
        c.execute('UPDATE email_runs SET status=?,detail=? WHERE id=?', ('Accepted by SMTP','Inbox delivery is not confirmed.',run_id))
        store.log(c, actor, 'SMTP accepted report', run_id)
    return True


def run_due(store, config, current=None):
    current = (current or datetime.now(TZ)).astimezone(TZ)
    s = store.setting('schedule', {})
    if not s.get('enabled') or current.date().isoformat() < s.get('start_date','9999'):
        return False
    if current.time().replace(tzinfo=None) < time.fromisoformat(s['time']):
        return False
    return dispatch(store,config,s['recipient'],'daily:'+current.date().isoformat())


def main():
    import streamlit as st
    st.set_page_config(page_title='Afrirent Depot Admin', layout='wide')
    st.title('Afrirent Depot Admin Controller')
    config = dict(os.environ)
    try:
        config.update({k: str(v) for k,v in st.secrets.items() if not isinstance(v,dict)})
    except FileNotFoundError:
        pass
    # Optional shared access gate; operator name below is self-declared, not verified identity.
    if config.get('APP_PASSWORD'):
        import hmac
        access = st.sidebar.text_input('App access password',type='password')
        if not hmac.compare_digest(access, config['APP_PASSWORD']):
            st.info('Enter the app access password to continue.')
            st.stop()
    actor = st.sidebar.text_input('Your name (for audit history)',max_chars=100).strip()
    st.sidebar.caption('Operator names are self-declared. Use managed authentication for verified identities.')
    db = Store(config.get('AFRIRENT_DB', str(ROOT/'data'/'depot.sqlite3')))
    if 'notice' in st.session_state:
        st.success(st.session_state.pop('notice'))
    def action(fn, message):
        try:
            required(actor,'Your name',100)
            result = fn()
            st.session_state['notice'] = message
            st.rerun()
        except (ValueError,RuntimeError,sqlite3.Error,OSError) as exc:
            st.error(str(exc))
    records = db.rows('records')
    tabs = st.tabs(['Capture','Search & edit','Reconciliation','Administration','Reports & email','Audit & import'])
    with tabs[0]:
        with st.form('capture',clear_on_submit=True):
            rid=st.text_input('Record ID / item code',max_chars=80)
            desc=st.text_area('Item description / category',max_chars=1000)
            qty=st.number_input('Quantity',min_value=0,max_value=1_000_000_000,value=1)
            if st.form_submit_button('Save record'):
                action(lambda:db.add(rid,desc,qty,actor),'Record saved.')
    with tabs[1]:
        query=st.text_input('Search ID or description').strip().casefold()
        filtered=[r for r in records if query in r['id'].casefold() or query in r['description'].casefold()]
        st.dataframe(pd.DataFrame(filtered),width='stretch')
        st.download_button('Download full Excel',export_excel(records),'depot_records.xlsx')
        if filtered:
            selected=st.selectbox('Record to edit or delete',[r['id'] for r in filtered])
            r=next(r for r in filtered if r['id']==selected)
            with st.form('edit_'+r['id']+'_'+str(r['version'])):
                desc=st.text_area('Description',value=r['description'],max_chars=1000)
                qty=st.number_input('Updated quantity',0,1_000_000_000,int(r['quantity']))
                if st.form_submit_button('Save changes'):
                    action(lambda:db.change(r['id'],desc,qty,actor,r['version']),'Changes saved.')
            with st.form('delete_'+r['id']+'_'+str(r['version'])):
                confirmation=st.text_input('Type the exact Record ID to confirm deletion')
                if st.form_submit_button('Delete record'):
                    if confirmation != r['id']:
                        st.error('Confirmation does not match.')
                    else:
                        action(lambda:db.change(r['id'],r['description'],r['quantity'],actor,r['version'],delete=True),'Record deleted; audit retained.')
    with tabs[2]:
        st.caption('Compare the stored quantity with a physical count. This records a variance; it does not automatically adjust stock.')
        if records:
            chosen=st.selectbox('Reconcile record',[r['id'] for r in records])
            r=next(r for r in records if r['id']==chosen)
            st.write('Stored quantity:',r['quantity'])
            with st.form('reconcile_'+r['id']+'_'+str(r['version'])):
                actual=st.number_input('Physical / verified quantity',0,1_000_000_000,int(r['quantity']))
                note=st.text_area('Evidence / reason for variance',max_chars=1000)
                if st.form_submit_button('Save reconciliation'):
                    action(lambda:db.reconcile(r['id'],actual,note,actor,r['version']),'Reconciliation saved. Review any variance before editing quantity.')
        st.dataframe(pd.DataFrame(db.rows('reconciliations')),width='stretch')
    with tabs[3]:
        st.caption('Track correspondence, appointments and supplier follow-ups. Drafts are downloaded for review; no supplier messages are sent automatically.')
        with st.form('admin',clear_on_submit=True):
            kind=st.selectbox('Type',KINDS)
            title=st.text_input('Subject / task',max_chars=200)
            contact=st.text_input('Contact / supplier / attendee',max_chars=200)
            due_date=st.date_input('Due date (SAST)',value=datetime.now(TZ).date())
            due_time=st.time_input('Due time (SAST)',value=time(9))
            details=st.text_area('Details / correspondence notes',max_chars=5000)
            if st.form_submit_button('Add administration task'):
                due=datetime.combine(due_date,due_time,tzinfo=TZ).isoformat()
                action(lambda:db.task(kind,title,contact,due,details,actor),'Task created.')
        tasks=sorted(db.rows('tasks'),key=lambda r:r['due'])
        overdue=[t for t in tasks if t['status'] not in ('Completed','Cancelled') and datetime.fromisoformat(t['due'])<datetime.now(TZ)]
        st.metric('Overdue open tasks',len(overdue))
        st.dataframe(pd.DataFrame(tasks),width='stretch')
        if tasks:
            tid=st.selectbox('Task to manage',[t['id'] for t in tasks],format_func=lambda k:next(t['title']+' — '+t['kind'] for t in tasks if t['id']==k))
            t=next(t for t in tasks if t['id']==tid)
            with st.form('status_'+tid+'_'+str(t['version'])):
                status=st.selectbox('Status',STATUSES,index=STATUSES.index(t['status']))
                if st.form_submit_button('Update status'):
                    action(lambda:db.task_status(tid,status,actor,t['version']),'Task status updated.')
            draft=f"Subject: {t['title']}\n\nDear {t['contact'] or 'Colleague'},\n\n{t['details']}\n\nPlease respond by {t['due']}.\n\nKind regards,\nDepot Administration\n"
            st.download_button('Download correspondence / follow-up draft',draft,'Afrirent_Draft.txt')
    with tabs[4]:
        scope=st.radio('Report records',['All records','Current search results'],horizontal=True)
        report_rows=records if scope=='All records' else filtered
        include_tasks=st.checkbox('Include all open administration tasks',value=True)
        st.write(f'{len(report_rows)} records; total quantity {sum(r["quantity"] for r in report_rows)}')
        # Generate only on request, not on every keystroke or tab rerun.
        if st.button('Generate PDF'):
            st.session_state['pdf']=make_pdf(report_rows,db.rows('tasks') if include_tasks else [],scope+(': '+query if scope!='All records' else ''))
            st.session_state['pdf_scope']=scope+'; generated '+now()
        if 'pdf' in st.session_state:
            st.caption('Prepared snapshot: '+st.session_state['pdf_scope']+'. Generate again after changing records or filters.')
            st.download_button('Download PDF',st.session_state['pdf'],'Afrirent_Depot_Report.pdf')
            with st.form('email'):
                recipient=st.text_input('Management recipient(s), comma separated',value=config.get('REPORT_TO',''))
                confirm=st.checkbox('Send this prepared report to these recipients')
                if st.form_submit_button('Send prepared PDF'):
                    if confirm:
                        action(lambda:dispatch(db,config,recipient,'manual:'+uuid4().hex,st.session_state['pdf'],actor),'SMTP server accepted the report; check inbox for delivery.')
                    else:
                        st.error('Confirm recipients before sending.')
        st.subheader('Daily report schedule')
        st.caption('Requires scheduled_report.py running on the SAME durable database. Daily reports include all current records and open tasks, not only that day’s activity. Missed days are not backfilled.')
        s=db.setting('schedule',{'enabled':False,'time':'16:00','recipient':config.get('REPORT_TO','')})
        with st.form('schedule'):
            enabled=st.checkbox('Enable daily email',value=s['enabled'])
            at=st.time_input('Daily send time (SAST)',value=time.fromisoformat(s['time']))
            to=st.text_input('Scheduled report recipients',value=s['recipient'])
            if st.form_submit_button('Save schedule'):
                action(lambda:db.schedule(enabled,at.strftime('%H:%M'),to,actor),'Schedule saved. The external worker must be running for automatic delivery.')
        st.dataframe(pd.DataFrame(db.rows('email_runs')),width='stretch')
    with tabs[5]:
        st.subheader('Audit history')
        st.dataframe(pd.DataFrame(db.rows('audit')),width='stretch')
        st.caption('History is retained by the app, but database administrators can alter it. Keep protected backups.')
        st.subheader('Import your existing depot_records.xlsx')
        st.caption('Atomic import: duplicates or invalid rows reject the whole file. Blank descriptions must be corrected first. Naive historical timestamps are interpreted as SAST.')
        uploaded=st.file_uploader('Original-format Excel file',type=['xlsx'])
        if uploaded and st.button('Import Excel records'):
            action(lambda:db.import_legacy(uploaded,actor),'Excel records imported.')


if __name__ == '__main__':
    main()
