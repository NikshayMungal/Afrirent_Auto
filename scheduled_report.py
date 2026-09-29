"""Run every five minutes with Windows Task Scheduler or cron. Sends only when enabled."""
import os
import sys
import tomllib
from Afrirent_Auto import ROOT, Store, run_due

if __name__ == '__main__':
    config = dict(os.environ)
    secrets = ROOT / '.streamlit' / 'secrets.toml'
    if secrets.exists():
        with secrets.open('rb') as f:
            config.update(tomllib.load(f))
    try:
        sent = run_due(Store(config.get('AFRIRENT_DB', str(ROOT/'data'/'depot.sqlite3'))),config)
        print('Report accepted by SMTP.' if sent else 'No report due, or today already attempted.')
    except Exception:
        print('Report attempt failed. Review email history and SMTP configuration; verify delivery before retrying.',file=sys.stderr)
        sys.exit(1)
