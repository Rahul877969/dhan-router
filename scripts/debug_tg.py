import os, requests
TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
API = f'https://api.telegram.org/bot{TOKEN}'

# Check webhook
wh = requests.get(f'{API}/getWebhookInfo').json()
print('WEBHOOK URL:', wh['result'].get('url', '(none)'))
print('PENDING UPDATES:', wh['result'].get('pending_update_count', 0))

# Check bot info
me = requests.get(f'{API}/getMe').json()
print('BOT:', me['result'].get('username'))

# Get updates with no filter
upd = requests.get(f'{API}/getUpdates', params={'limit':5, 'timeout':0}).json()
print('RAW UPDATES:', upd)