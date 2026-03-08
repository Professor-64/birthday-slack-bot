import os, json, gspread, requests, random
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from dotenv import load_dotenv

def get_random_birthday_gif(api_key):
    url = "https://api.giphy.com/v1/gifs/search"
    params = {"api_key": api_key, "q": "birthday", "limit": 25, "offset": random.randint(0, 50), "rating": "pg-13", "lang": "en"}
    r = requests.get(url, params=params)
    r.raise_for_status()
    data = r.json()
    return data["data"][0]["images"]["original"]["url"] if data.get("data") else None

def parse_dag_maand(datum_str):
    datum_str = str(datum_str).strip()
    if not datum_str:
        return None
    formaten = ["%d-%m-%Y", "%d/%m/%Y", "%d-%m", "%d/%m"]
    for fmt in formaten:
        try:
            if fmt in ("%d-%m", "%d/%m"):
                datum_str_jaar = f"{datum_str}-2000" if '-' in datum_str else f"{datum_str}/2000"
                fmt += "-%Y"
                d = datetime.strptime(datum_str_jaar, fmt)
            else:
                d = datetime.strptime(datum_str, fmt)
            return (d.day, d.month)
        except ValueError:
            continue
    return None

def format_namelist(namen):
    return namen[0] if len(namen) == 1 else ', '.join(namen[:-1]) + ' en ' + namen[-1]

def verstuur_wens(client, channel, bericht, gif_url=None):
    try:
        if gif_url:
            client.chat_postMessage(
                channel=channel,
                text=bericht,
                blocks=[
                    {"type": "section", "text": {"type": "mrkdwn", "text": bericht}},
                    {"type": "image", "image_url": gif_url, "alt_text": "Verjaardagsgif"},
                ],
            )
        else:
            raise ValueError("no_gif")
    except Exception:
        client.chat_postMessage(channel=channel, text=bericht)

def get_sheet(gc, sheet_key, sheet_url):
    try:
        return gc.open_by_key(sheet_key).sheet1
    except gspread.SpreadsheetNotFound:
        try:
            return gc.open_by_url(sheet_url).sheet1
        except gspread.SpreadsheetNotFound:
            return None

if __name__ == "__main__":
    load_dotenv()
    client = WebClient(token=os.environ["SLACK_TOKEN"])
    giphy_api_key = os.environ["GIPHY_TOKEN"]
    scope = ['https://spreadsheets.google.com/feeds','https://www.googleapis.com/auth/drive']
    creds_info = json.loads(os.environ.get("GCP_CREDENTIALS") or "{}")
    if not creds_info:
        raise RuntimeError("GCP_CREDENTIALS ontbreekt als environment variable.")
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_info, scope)
    gc = gspread.authorize(creds)

    sheet = get_sheet(gc, os.environ["SHEET_KEY"], os.environ["SHEET_URL"])
    if not sheet:
        raise RuntimeError("Kon de Google Sheet niet vinden. Controleer SHEET_KEY/SHEET_URL en permissies.")
    data = sheet.get_all_records()

    vandaag_tuple = (datetime.now().day, datetime.now().month)
    jarigen = [p for p in data if parse_dag_maand(p.get('Geboortedatum')) == vandaag_tuple]

    vandaag = datetime.now().strftime("%d/%m/%Y")

    if not jarigen:
        print(f"{vandaag}: Geen jarigen vandaag.")
    else:
        channel = "#avo-random"
        mentions, namen_jarigen = [], []
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
                mentions.append(voornaam)

        with open('verjaardagswensen.json', 'r', encoding='utf-8') as f:
            WENSEN = json.load(f)

        wens = random.choice(WENSEN)
        wens_tekst = (
            wens['singular'].replace('{name}', mentions[0])
            if len(mentions) == 1
            else wens['plural'].replace('{names}', format_namelist(mentions))
        )

        gif = get_random_birthday_gif(giphy_api_key)
        verstuur_wens(client, channel, bericht="🥳🎉 \n" + wens_tekst, gif_url=gif)
        print(f"{vandaag}: Verjaardagswens verstuurd voor: {format_namelist(namen_jarigen)}")
