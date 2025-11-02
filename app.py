import os, json, random, sqlite3, requests, gspread
from datetime import datetime
from flask import Flask, request
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from slack_bolt.oauth import OAuthSettings
from slack_bolt.oauth.oauth_settings import OAuthInstallationStore, OAuthStateStore
from slack_bolt.oauth.installation_store.file import FileInstallationStore
from slack_bolt.oauth.state_store.file import FileOAuthStateStore
from oauth2client.service_account import ServiceAccountCredentials
from apscheduler.schedulers.background import BackgroundScheduler

# ---------- Storage ----------
os.makedirs("./data", exist_ok=True)
INSTALL_STORE = FileInstallationStore(base_dir="./data/installations")
STATE_STORE = FileOAuthStateStore(expiration_seconds=600, base_dir="./data/states")

def db():
    conn = sqlite3.connect("./data/birthday.db")
    conn.row_factory = sqlite3.Row
    return conn

with db() as c:
    c.execute("""CREATE TABLE IF NOT EXISTS channel_configs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      team_id TEXT NOT NULL,
      channel_id TEXT NOT NULL,
      sheet_url TEXT NOT NULL,
      UNIQUE(team_id, channel_id))""")

# ---------- Google Sheets ----------
SCOPE = ['https://spreadsheets.google.com/feeds','https://www.googleapis.com/auth/drive']
CREDS = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(os.environ["GCP_CREDENTIALS"]), SCOPE)

def gsheet_records(sheet_url: str):
    gc = gspread.authorize(CREDS)
    sh = gc.open_by_url(sheet_url).sheet1
    return sh.get_all_records()

# ---------- Utils ----------
def parse_dag_maand(s):
    s = (s or "").strip()
    if not s: return None
    for fmt in ("%d-%m-%Y","%d/%m/%Y","%d-%m","%d/%m"):
        try:
            if fmt in ("%d-%m","%d/%m"):
                s2 = f"{s}-2000" if '-' in s else f"{s}/2000"
                d = datetime.strptime(s2, fmt+"-%Y")
            else:
                d = datetime.strptime(s, fmt)
            return (d.day, d.month)
        except: pass
    return None

def get_gif(api_key):
    r = requests.get("https://api.giphy.com/v1/gifs/search", params={
        "api_key": api_key, "q":"birthday", "limit":25, "offset":random.randint(0,50), "rating":"pg-13", "lang":"en"
    })
    r.raise_for_status()
    data = r.json()
    return data["data"][0]["images"]["original"]["url"] if data.get("data") else None

def name_join(names):
    return names[0] if len(names)==1 else ", ".join(names[:-1]) + " en " + names[-1]

# ---------- Slack Bolt (OAuth) ----------
app = App(
    signing_secret=os.environ["SLACK_SIGNING_SECRET"],
    oauth_settings=OAuthSettings(
        client_id=os.environ["SLACK_CLIENT_ID"],
        client_secret=os.environ["SLACK_CLIENT_SECRET"],
        scopes=["chat:write","users:read.email","chat:write.public","commands"],
        installation_store=INSTALL_STORE,
        state_store=STATE_STORE,
        user_scopes=[]
    ),
)

flask_app = Flask(__name__)
handler = SlackRequestHandler(app)

# ---------- App Home ----------
def publish_home(client, user_id, team_id):
    with db() as c:
        rows = c.execute("SELECT channel_id, sheet_url FROM channel_configs WHERE team_id=?", (team_id,)).fetchall()
    configs_md = "\n".join([f"• <#{r['channel_id']}> → {r['sheet_url']}" for r in rows]) or "_Nog geen koppelingen_"

    client.views_publish(
        user_id=user_id,
        view={
          "type": "home",
          "blocks": [
            {"type":"section","text":{"type":"mrkdwn","text":"*Verjaardagsbot* – koppel een Sheet aan een kanaal."}},
            {"type":"section","text":{"type":"mrkdwn","text":configs_md}},
            {"type":"actions","elements":[
              {"type":"button","text":{"type":"plain_text","text":"Koppeling toevoegen"}, "action_id":"open_config_modal"}
            ]}
          ]
        }
    )

@app.event("app_home_opened")
def on_home_opened(body, client, logger):
    publish_home(client, body["event"]["user"], body["team_id"])

@app.action("open_config_modal")
def open_modal(ack, body, client):
    ack()
    client.views_open(
        trigger_id=body["trigger_id"],
        view={
          "type":"modal",
          "callback_id":"config_submit",
          "title":{"type":"plain_text","text":"Koppel Sheet & Kanaal"},
          "submit":{"type":"plain_text","text":"Opslaan"},
          "blocks":[
            {"type":"input","block_id":"b_sheet","element":{"type":"plain_text_input","action_id":"sheet_url","placeholder":{"type":"plain_text","text":"https://docs.google.com/..."}}, "label":{"type":"plain_text","text":"Google Sheet URL"}},
            {"type":"input","block_id":"b_chan","element":{"type":"conversations_select","action_id":"chan","default_to_current_conversation":True}, "label":{"type":"plain_text","text":"Kanaal"}}
          ]
        }
    )

@app.view("config_submit")
def handle_view_submission(ack, body, client):
    ack()
    team_id = body["team"]["id"]
    sheet_url = body["view"]["state"]["values"]["b_sheet"]["sheet_url"]["value"].strip()
    channel_id = body["view"]["state"]["values"]["b_chan"]["chan"]["selected_conversation"]
    with db() as c:
        c.execute("INSERT OR REPLACE INTO channel_configs(team_id,channel_id,sheet_url) VALUES(?,?,?)",
                  (team_id, channel_id, sheet_url))
    publish_home(client, body["user"]["id"], team_id)

# ---------- (Optioneel) Slash command als alternatief configuratiepad ----------
@app.command("/birthday-setup")
def cmd_setup(ack, respond, body):
    ack()
    text = (body.get("text") or "").strip()
    if not text.startswith("http"):
        respond("Geef een geldige Google Sheet URL.")
        return
    with db() as c:
        c.execute("INSERT OR REPLACE INTO channel_configs(team_id,channel_id,sheet_url) VALUES(?,?,?)",
                  (body["team_id"], body["channel_id"], text))
    respond("✅ Koppeling opgeslagen.")

# ---------- Dagelijkse job ----------
def run_daily():
    vandaag = (datetime.now().day, datetime.now().month)
    giphy = os.environ.get("GIPHY_TOKEN")
    with db() as c:
        configs = c.execute("SELECT * FROM channel_configs").fetchall()
    for cfg in configs:
        inst = INSTALL_STORE.find_bot(team_id=cfg["team_id"])
        if not inst: continue
        client = app.client  # reuse Bolt client
        client.token = inst.bot_token
        try:
            data = gsheet_records(cfg["sheet_url"])
            jarigen = [p for p in data if parse_dag_maand(p.get("Verjaardag")) == vandaag]
            if not jarigen: continue
            mentions = []
            for p in jarigen:
                email = (p.get("E-mail") or "").strip()
                try:
                    uid = client.users_lookupByEmail(token=inst.bot_token, email=email)["user"]["id"]
                    mentions.append(f"<@{uid}>")
                except:  # fallback
                    mentions.append(p.get("Voornaam","(onbekend)"))
            with open('verjaardagswensen.json','r',encoding='utf-8') as f:
                W = json.load(f)
            wens = random.choice(W)
            text = (wens["singular"].replace("{name}", mentions[0]) if len(mentions)==1
                    else wens["plural"].replace("{names}", ", ".join(mentions[:-1]) + " en " + mentions[-1]))
            gif = get_gif(giphy) if giphy else None
            try:
                if gif:
                    client.chat_postMessage(token=inst.bot_token, channel=cfg["channel_id"], text="🥳🎉 \n"+text, blocks=[
                        {"type":"section","text":{"type":"mrkdwn","text":"🥳🎉 \n"+text}},
                        {"type":"image","image_url":gif,"alt_text":"birthday"}
                    ])
                else:
                    raise ValueError("no_gif")
            except:
                client.chat_postMessage(token=inst.bot_token, channel=cfg["channel_id"], text="🥳🎉 \n"+text)
        except Exception as e:
            print(f"[{cfg['team_id']}|{cfg['channel_id']}] fout: {e}")

sched = BackgroundScheduler()
sched.add_job(run_daily, "cron", hour=8, minute=0, timezone="Europe/Brussels")
sched.start()

# ---------- Flask endpoints ----------
@flask_app.route("/slack/install", methods=["GET"])
def install():
    return handler.handle(request)

@flask_app.route("/slack/oauth_redirect", methods=["GET"])
def oauth_redirect():
    return handler.handle(request)

@flask_app.route("/slack/events", methods=["POST"])
def events():
    return handler.handle(request)

@flask_app.get("/healthz")
def health():
    return "ok", 200

@app.event("app_uninstalled")
def on_uninstalled(body, logger):
    team_id = body.get("team_id")
    if not team_id: return
    with db() as c:
        c.execute("DELETE FROM channel_configs WHERE team_id=?", (team_id,))

if __name__ == "__main__":
    flask_app.run(host="0.0.0.0", port=8080)
