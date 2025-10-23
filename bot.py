import os, json, gspread, requests, random
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from dotenv import load_dotenv

def get_random_birthday_gif(api_key):
    url = "https://api.giphy.com/v1/gifs/search"
    params = {
        "api_key": api_key,
        "q": "birthday",
        "limit": 25,
        "offset": random.randint(0, 50),
        "rating": "pg-13",  # <-- met streepje
        "lang": "en"
    }
    r = requests.get(url, params=params)
    r.raise_for_status()
    data = r.json()
    if data.get("data"):
        return data["data"][0]["images"]["original"]["url"]
    return None

# Load env
load_dotenv()
slack_token = os.environ['SLACK_TOKEN']
giphy_api_key = os.environ['GIPHY_TOKEN']
client = WebClient(token=slack_token)

# Google Sheets setup via ENV JSON
scope = ['https://spreadsheets.google.com/feeds','https://www.googleapis.com/auth/drive']

creds_json = os.environ.get('GCP_CREDENTIALS')
if not creds_json:
    raise RuntimeError("GCP_CREDENTIALS ontbreekt als environment variable.")
creds_info = json.loads(creds_json)
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_info, scope)
gc = gspread.authorize(creds)

sheet_key = os.environ['SHEET_KEY']
sheet_url = os.environ['SHEET_URL']

try:
    sheet = gc.open_by_key(sheet_key).sheet1
except gspread.SpreadsheetNotFound:
    try:
        sheet = gc.open_by_url(sheet_url).sheet1
    except gspread.SpreadsheetNotFound:
        sheet = None

if not sheet:
    raise RuntimeError("Kon de Google Sheet niet vinden. Controleer SHEET_KEY/SHEET_URL en permissies.")

data = sheet.get_all_records()

# Huidige datum
vandaag = datetime.now().strftime("%d-%m")

# Filter jarigen
jarigen = [p for p in data if str(p.get('Verjaardag', '')).strip() == vandaag]


if jarigen:
    # Slack-mentions ophalen
    mentions = []
    namen_jarigen = []

    for p in jarigen:
        email = p.get('E-mail', '').strip()
        voornaam = p.get('Voornaam', '(onbekend)')
        namen_jarigen.append(voornaam)

        try:
            resp = client.users_lookupByEmail(email=email)
            user_id = resp['user']['id']
            mentions.append(f"<@{user_id}>")
        except SlackApiError as e:
            print(f"Kan gebruiker niet vinden voor {email}: {e.response.get('error')}")
            mentions.append(voornaam)  # fallback zonder Slack-ID

    # Hulpfunctie om namen samen te voegen
    def format_namelist(namen):
        if len(namen) == 1:
            return namen[0]
        return ', '.join(namen[:-1]) + ' en ' + namen[-1]

    # Verjaardagswensen laden
    with open('verjaardagswensen.json', 'r', encoding='utf-8') as f:
        WENSEN = json.load(f)

    # Random wens kiezen
    wens = random.choice(WENSEN)
    if len(mentions) == 1:
        wens_tekst = wens['singular'].replace('{name}', mentions[0])
    else:
        wens_tekst = wens['plural'].replace('{names}', format_namelist(mentions))

    # GIF ophalen
    gif_url = get_random_birthday_gif(giphy_api_key)

    # Bericht opstellen
    bericht = wens_tekst

    # Bericht versturen
    if gif_url:
        client.chat_postMessage(
            channel='#avo-testverjaardagen',
            text=bericht,
            blocks=[
                {"type": "section", "text": {"type": "mrkdwn", "text": bericht}},
                {"type": "image", "image_url": gif_url, "alt_text": "Verjaardagsgif"}
            ]
        )
    else:
        client.chat_postMessage(channel='#avo-testverjaardagen', text=bericht)
else:
    print("Geen jarigen vandaag.")
