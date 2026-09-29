# Afrirent Depot Admin — updated application

This extends the uploaded inventory/depot record app. It retains Record ID, Item Description and Quantity. It does not introduce the vehicle/driver/fuel fields described in the earlier fleet overview.

## Included features

- Create and edit records; case-insensitive duplicate IDs blocked; required fields and whole-number quantities validated.
- Literal-text search (characters such as `[` no longer act as regular expressions).
- Confirmed deletion of one unique record; audit history with before/after values and operator name.
- Physical-count reconciliation with stored quantity, actual quantity, variance and note. Stock is only corrected through an explicit edit after review.
- Landscape PDF, wrapping text, repeated table headers, filtered or full reports, record count and quantity total. Optional open-task section.
- Configurable SMTP host, TLS mode, saved credentials, manual report sending and email attempt history.
- Daily scheduled report worker, SAST send time and recipients configured in the app. Disabled until enabled and the worker is installed.
- Correspondence log, appointments, supplier follow-ups, general tasks, due dates, overdue count, status changes and downloadable correspondence drafts.
- Original-format Excel import and export. Data is now stored in SQLite, not rewritten into the old Excel file after every action.

## Windows setup (Python 3.11 or newer)

1. Extract the ZIP to a permanent folder, for example `C:\Afrirent`. Keep all files together.
2. Back up your original Python file and `depot_records.xlsx`.
3. Open PowerShell in the extracted folder and run:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run Afrirent_Auto.py
```

Use your installed Python 3.11+ version if `py -3.11` is unavailable. The app opens in your browser. Enter your operator name before saving changes. Operator names are self-declared and are not proof of identity.

4. To migrate existing records, select **Audit & import**, upload the old `depot_records.xlsx`, and click **Import Excel records**. Required columns are `Record ID`, `Item / Description`, `Quantity`, `Date Added`. Resolve duplicate IDs and blank descriptions first. A validation error rolls back the entire import. The original file is not modified. Imported timestamps lacking a timezone are assumed to be SAST; correct the source first if that assumption is wrong.
5. Use **Search & edit** to correct records, and **Reconciliation** to log physical counts.

## Email configuration

Create a `.streamlit` folder next to the app. Copy `secrets.example.toml` into it and rename it `secrets.toml`. Replace all placeholder values. Never commit real credentials. The optional `APP_PASSWORD` provides a shared access gate; it does not provide individual permissions or verified audit identities. Remove that setting only for a controlled local test.

The app supports environment variables too. Local secret-file values take precedence. The worker reads the same `.streamlit/secrets.toml` file. Provider settings vary: use your provider's host, port and app password. Use `starttls` (usually 587) or `ssl` (usually 465). Plain, unencrypted SMTP is not offered.

Use **Reports & email → Generate PDF**. Review the download, enter your test inbox, check the confirmation box, and send. The app records SMTP acceptance, which is not a guarantee of inbox delivery. No email was sent while developing this update.

Streamlit secrets reference: https://docs.streamlit.io/develop/api-reference/connections/st.secrets

## Automatic daily emails — Windows Task Scheduler

The Streamlit page alone does not run scheduled jobs while closed. Install the worker on an always-on machine with access to the same local database as the app.

1. In **Reports & email**, set the daily SAST time and recipients, enable daily email, and save the schedule.
2. Open **Task Scheduler → Create Task**. Name it `Afrirent scheduled reports`.
3. Add a daily trigger, repeating every **5 minutes** for **1 day**, enabled. Under Settings, prevent overlapping instances and allow missed starts to run when available. Use an account with access to the database and secrets file.
4. Add **Start a program**:
   - Program: `C:\Afrirent\.venv\Scripts\python.exe`
   - Arguments: `"C:\Afrirent\scheduled_report.py"`
   - Start in: `C:\Afrirent`
5. Select **Run whether user is logged on or not** if your environment permits it. Keep the machine awake, or configure wake-to-run appropriately.
6. Run the task manually after the configured send time using a test recipient; inspect **email history** in the app.

Daily reports are full current snapshots including open admin tasks. They are not date-filtered daily transactions. They send on weekends too. The first worker run at or after the time attempts that day's report once. Earlier missed days are not backfilled. A disabled schedule sends nothing.

An atomic database claim prevents duplicate automatic attempts for the same SAST date. On a failure, process crash or timeout, that day's run is deliberately not retried automatically because SMTP delivery may be uncertain. Verify delivery, then use manual sending if needed. Next day's run remains eligible. This avoids blind retries; it is not an exactly-once delivery guarantee.

## Hosting and durability

This package is not deployed to your live Streamlit URL. For a local installation or persistent server, the app and worker must use the SAME database file. `AFRIRENT_DB` can set an absolute path; the default is `data/depot.sqlite3` beside the code. Use a local persistent disk and a single app host; do not put SQLite on a general network share.

Do not assume a hosted app's local filesystem is durable. Before using this for live business operations on a managed or ephemeral host, provide persistent database storage. A worker running on your laptop cannot access a cloud app's private SQLite file. A shared managed database and hosted scheduler would require a further deployment-specific integration. This package does not claim to configure those services.

Stop app and worker before copying the database for backup, or use SQLite's backup API. The database contains records, tasks, reconciliation, settings, audit and email history. Excel exports contain records only and are NOT full backups. Audit history has no delete control in the app, but is not tamper-proof against database administrators.

## Scope of the administration features

Staff still enter and verify information, perform physical counts, decide how to handle variances, review correspondence and contact suppliers. Appointments are an internal tracker; there is no Outlook/Google Calendar sync. Correspondence drafts are generated for review, not sent automatically. The daily management report includes open tasks; it is not an individual reminder email service.

## Testing

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt
.\.venv\Scripts\python.exe -m unittest -v test_afrirent.py
.\.venv\Scripts\python.exe test_ui.py
```

Tests use temporary databases and mocked SMTP. They cover duplicates, validation, transaction rollback, stale edits, deletion history, reconciliation snapshots, tasks, Excel migration/export, PDF pagination and scheduled sending. See VALIDATION.txt for the actual execution result and remaining checks.

The included Unicode font covers common Latin names; confirm unusual scripts or emoji against the fonts required for your depot. Font licence is included. Dependency versions are compatible ranges, not a fully locked production environment.
