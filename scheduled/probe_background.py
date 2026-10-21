"""No-message/no-model fixture for Windows background analysis capability."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(r'C:\Trading')
sys.path.insert(0, str(ROOT))
from dotenv import dotenv_values
import requests
from browser_research import CanadianResearchBrowser

result = {}
try:
    values = dotenv_values(ROOT / '.env')
    result['configuration_readable'] = bool(values.get('TELEGRAM_BOT_TOKEN'))
    with (ROOT / 'claude_news_session.session').open('rb') as handle:
        result['news_session_readable'] = bool(handle.read(1))
except Exception:
    result['configuration_readable'] = False
try:
    response = requests.get('https://query1.finance.yahoo.com/v8/finance/chart/XIC.TO',
                            params={'range': '5d', 'interval': '1d'}, timeout=15,
                            headers={'User-Agent': 'Mozilla/5.0'})
    result['public_data_access'] = response.status_code == 200
except Exception:
    result['public_data_access'] = False
try:
    browser = CanadianResearchBrowser()
    check = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                            '-o', 'ConnectTimeout=3', browser.target, 'true'],
                           capture_output=True, timeout=10)
    result['ssh_key_access'] = check.returncode == 0
except Exception:
    result['ssh_key_access'] = False
try:
    import session_runner
    import telegram_notify
    result['runner_importable'] = True
    result['telegram_configuration_matches'] = (
        telegram_notify.TOKEN == values.get('TELEGRAM_BOT_TOKEN') and
        telegram_notify.CHATID == values.get('TELEGRAM_CHAT_ID'))
    response = requests.get('https://api.telegram.org/bot' + telegram_notify.TOKEN + '/getMe',
                            timeout=10)
    result['telegram_bot_readable'] = response.status_code == 200 and response.json().get('ok') is True
except Exception:
    result['telegram_bot_readable'] = False
print(json.dumps(result))
