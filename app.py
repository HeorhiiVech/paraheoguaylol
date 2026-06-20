import json
import struct
import sqlite3
import hashlib
import urllib.parse
from flask import Flask, request, render_template, jsonify, g

app = Flask(__name__)

DDRAGON_VER = "16.5.1"
DATABASE = 'scouthub.db'

# ==============================================================================
#  TEAM ROSTER — put the RIOT_ID_GAME_NAME of your selected players here.
#  These names are highlighted on every page and drive the Team Stats page.
#  Currently filled with the BLUE side players from LA2-1589059802 for testing.
# ==============================================================================
ROSTER = {
    "TOP":     ["Malik Shadman"],
    "JUNGLE":  ["Purdycaccinho"],
    "MIDDLE":  ["Iinelessaus"],
    "BOTTOM":  ["Meneo"],
    "UTILITY": ["Kiddoxx", "Perreo"],
}
ROSTER_NAMES = {name for names in ROSTER.values() for name in names}
# ==============================================================================


def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db


@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()


def init_db():
    with app.app_context():
        db = get_db()
        cursor = db.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS matches (
                id TEXT PRIMARY KEY,
                duration INTEGER,
                winner TEXT,
                blue_kills INTEGER DEFAULT 0,
                red_kills INTEGER DEFAULT 0,
                blue_dragons INTEGER DEFAULT 0,
                red_dragons INTEGER DEFAULT 0,
                blue_barons INTEGER DEFAULT 0,
                red_barons INTEGER DEFAULT 0,
                blue_heralds INTEGER DEFAULT 0,
                red_heralds INTEGER DEFAULT 0,
                blue_grubs INTEGER DEFAULT 0,
                red_grubs INTEGER DEFAULT 0,
                blue_atakhan INTEGER DEFAULT 0,
                red_atakhan INTEGER DEFAULT 0
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS player_stats (
                match_id TEXT,
                name TEXT,
                champion TEXT,
                team TEXT,
                role TEXT,
                win INTEGER,
                kills INTEGER,
                deaths INTEGER,
                assists INTEGER,
                damage INTEGER,
                damage_taken INTEGER,
                self_mitigated INTEGER,
                heal_shield INTEGER,
                gold INTEGER,
                minions INTEGER,
                vision_score INTEGER,
                wards_placed INTEGER,
                wards_killed INTEGER,
                control_wards INTEGER,
                control_wards_bought INTEGER DEFAULT 0,
                detector_wards INTEGER DEFAULT 0,
                cc_time INTEGER,
                cc_score INTEGER DEFAULT 0,
                time_dead INTEGER DEFAULT 0,
                time_played INTEGER DEFAULT 0,
                takedowns_15 INTEGER DEFAULT 0,
                enemy_jungle_cs INTEGER DEFAULT 0,
                own_jungle_cs INTEGER DEFAULT 0,
                dmg_objectives INTEGER,
                dmg_turrets INTEGER,
                dmg_buildings INTEGER DEFAULT 0,
                turret_takedowns INTEGER,
                largest_multikill INTEGER,
                first_blood INTEGER DEFAULT 0,
                dragons INTEGER,
                barons INTEGER,
                heralds INTEGER,
                grubs INTEGER,
                atakhan INTEGER,
                keystone INTEGER,
                summoner1 INTEGER,
                summoner2 INTEGER,
                item0 INTEGER, item1 INTEGER, item2 INTEGER, item3 INTEGER,
                item4 INTEGER, item5 INTEGER, item6 INTEGER,
                FOREIGN KEY(match_id) REFERENCES matches(id)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS player_profiles (
                name TEXT PRIMARY KEY,
                is_starred INTEGER DEFAULT 0,
                note TEXT DEFAULT ''
            )
        ''')
        db.commit()


init_db()


def extract_rofl_metadata(file_bytes):
    try:
        if len(file_bytes) < 4:
            return {"error": "File too small"}
        length_bytes = file_bytes[-4:]
        metadata_length = struct.unpack("<I", length_bytes)[0]
        start_index = len(file_bytes) - (metadata_length + 4)
        json_bytes = file_bytes[start_index: len(file_bytes) - 4]
        parsed_metadata = json.loads(json_bytes.decode("utf-8"))
        if "statsJson" in parsed_metadata:
            parsed_metadata["statsJson"] = json.loads(parsed_metadata["statsJson"])
        return parsed_metadata
    except Exception as e:
        return {"error": str(e)}


def safe_int(value):
    try:
        return int(value)
    except (ValueError, TypeError):
        return 0


def champ_icon(name):
    return f"http://ddragon.leagueoflegends.com/cdn/{DDRAGON_VER}/img/champion/{name}.png"


def item_icon(item_id):
    return f"http://ddragon.leagueoflegends.com/cdn/{DDRAGON_VER}/img/item/{item_id}.png"


# ---------- REPLAY INGESTION ----------

def process_replay(file_bytes, cursor):
    metadata = extract_rofl_metadata(file_bytes)
    if "error" in metadata:
        return False
    stats = metadata.get("statsJson")
    if not stats:
        return False

    match_id = hashlib.md5(file_bytes[:1000]).hexdigest()
    duration = int(safe_int(metadata.get("gameLength", 0)) / 1000)

    cursor.execute("SELECT id FROM matches WHERE id = ?", (match_id,))
    if cursor.fetchone():
        return False

    # Team objective aggregates
    agg = {"100": {}, "200": {}}
    for side in ("100", "200"):
        agg[side] = {"kills": 0, "dragons": 0, "barons": 0, "heralds": 0, "grubs": 0, "atakhan": 0}

    winner = "Unknown"
    # Earliest first kill -> First Blood
    earliest_fb = None

    rows = []
    for p in stats:
        team = str(p.get("TEAM"))
        is_win = 1 if str(p.get("WIN", "")).lower() == "win" else 0
        if is_win:
            winner = "Blue" if team == "100" else "Red"

        dragons = safe_int(p.get("DRAGON_KILLS"))
        barons = safe_int(p.get("BARON_KILLS"))
        heralds = safe_int(p.get("RIFT_HERALD_KILLS"))
        grubs = safe_int(p.get("HORDE_KILLS"))
        atakhan = safe_int(p.get("ATAKHAN_KILLS"))
        kills = safe_int(p.get("CHAMPIONS_KILLED"))

        if team in agg:
            agg[team]["kills"] += kills
            agg[team]["dragons"] += dragons
            agg[team]["barons"] += barons
            agg[team]["heralds"] += heralds
            agg[team]["grubs"] += grubs
            agg[team]["atakhan"] += atakhan

        rows.append((p, team, is_win, dragons, barons, heralds, grubs, atakhan, kills))

    cursor.execute('''
        INSERT INTO matches (
            id, duration, winner,
            blue_kills, red_kills,
            blue_dragons, red_dragons, blue_barons, red_barons,
            blue_heralds, red_heralds, blue_grubs, red_grubs,
            blue_atakhan, red_atakhan
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    ''', (
        match_id, duration, winner,
        agg["100"]["kills"], agg["200"]["kills"],
        agg["100"]["dragons"], agg["200"]["dragons"],
        agg["100"]["barons"], agg["200"]["barons"],
        agg["100"]["heralds"], agg["200"]["heralds"],
        agg["100"]["grubs"], agg["200"]["grubs"],
        agg["100"]["atakhan"], agg["200"]["atakhan"],
    ))

    for (p, team, is_win, dragons, barons, heralds, grubs, atakhan, kills) in rows:
        cursor.execute('''
            INSERT INTO player_stats (
                match_id, name, champion, team, role, win,
                kills, deaths, assists, damage, damage_taken, self_mitigated, heal_shield,
                gold, minions, vision_score, wards_placed, wards_killed, control_wards,
                control_wards_bought, detector_wards,
                cc_time, cc_score, time_dead, time_played, takedowns_15,
                enemy_jungle_cs, own_jungle_cs,
                dmg_objectives, dmg_turrets, dmg_buildings, turret_takedowns, largest_multikill, first_blood,
                dragons, barons, heralds, grubs, atakhan,
                keystone, summoner1, summoner2,
                item0, item1, item2, item3, item4, item5, item6
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', (
            match_id,
            p.get("RIOT_ID_GAME_NAME"),
            p.get("SKIN"),
            team,
            p.get("TEAM_POSITION") or p.get("INDIVIDUAL_POSITION", "UNKNOWN"),
            is_win,
            kills,
            safe_int(p.get("NUM_DEATHS")),
            safe_int(p.get("ASSISTS")),
            safe_int(p.get("TOTAL_DAMAGE_DEALT_TO_CHAMPIONS")),
            safe_int(p.get("TOTAL_DAMAGE_TAKEN")),
            safe_int(p.get("TOTAL_DAMAGE_SELF_MITIGATED")),
            safe_int(p.get("TOTAL_HEAL_ON_TEAMMATES")) + safe_int(p.get("TOTAL_DAMAGE_SHIELDED_ON_TEAMMATES")),
            safe_int(p.get("GOLD_EARNED")),
            safe_int(p.get("MINIONS_KILLED")) + safe_int(p.get("NEUTRAL_MINIONS_KILLED")),
            safe_int(p.get("VISION_SCORE")),
            safe_int(p.get("WARD_PLACED")),
            safe_int(p.get("WARD_KILLED")),
            safe_int(p.get("VISION_WARDS_BOUGHT_IN_GAME")),
            safe_int(p.get("VISION_WARDS_BOUGHT_IN_GAME")),
            safe_int(p.get("WARD_PLACED_DETECTOR")),
            safe_int(p.get("TOTAL_TIME_CROWD_CONTROL_DEALT_TO_CHAMPIONS")),
            safe_int(p.get("TIME_CCING_OTHERS")),
            safe_int(p.get("TOTAL_TIME_SPENT_DEAD")),
            safe_int(p.get("TIME_PLAYED")),
            safe_int(p.get("Missions_TakedownsBefore15Min")),
            safe_int(p.get("NEUTRAL_MINIONS_KILLED_ENEMY_JUNGLE")),
            safe_int(p.get("NEUTRAL_MINIONS_KILLED_YOUR_JUNGLE")),
            safe_int(p.get("TOTAL_DAMAGE_DEALT_TO_OBJECTIVES")),
            safe_int(p.get("TOTAL_DAMAGE_DEALT_TO_TURRETS")),
            safe_int(p.get("TOTAL_DAMAGE_DEALT_TO_BUILDINGS")),
            safe_int(p.get("TURRET_TAKEDOWNS")),
            safe_int(p.get("LARGEST_MULTI_KILL")),
            0,
            dragons, barons, heralds, grubs, atakhan,
            safe_int(p.get("KEYSTONE_ID")),
            safe_int(p.get("SUMMONER_SPELL_1")),
            safe_int(p.get("SUMMONER_SPELL_2")),
            safe_int(p.get("ITEM0")), safe_int(p.get("ITEM1")), safe_int(p.get("ITEM2")),
            safe_int(p.get("ITEM3")), safe_int(p.get("ITEM4")), safe_int(p.get("ITEM5")),
            safe_int(p.get("ITEM6")),
        ))
    return True


# ---------- MATCH HISTORY ----------

@app.route('/', methods=['GET', 'POST'])
def index():
    db = get_db()
    cursor = db.cursor()

    if request.method == 'POST':
        files = request.files.getlist('files')
        for file in files:
            if file.filename == '':
                continue
            process_replay(file.read(), cursor)
        db.commit()

    cursor.execute("SELECT * FROM matches ORDER BY rowid DESC LIMIT 25")
    matches_rows = cursor.fetchall()

    games_history = []
    for m in matches_rows:
        cursor.execute("SELECT * FROM player_stats WHERE match_id = ?", (m["id"],))
        participants = cursor.fetchall()
        match_mins = m["duration"] / 60.0 if m["duration"] else 1

        blue_team, red_team = [], []
        for p in participants:
            inv = [item_icon(p[f'item{i}']) for i in range(7) if p[f'item{i}'] > 0]
            p_data = {
                "name": p["name"],
                "icon": champ_icon(p["champion"]),
                "kda": f"{p['kills']}/{p['deaths']}/{p['assists']}",
                "dpm": int(p["damage"] / match_mins) if match_mins > 0 else 0,
                "win": p["win"],
                "inventory": inv,
                "is_roster": p["name"] in ROSTER_NAMES,
            }
            if p["team"] == "100":
                blue_team.append(p_data)
            else:
                red_team.append(p_data)

        winner_text = "Blue win" if m["winner"] == "Blue" else ("Red win" if m["winner"] == "Red" else "Unknown")

        games_history.append({
            "id": m["id"][:8],
            "duration": f"{m['duration'] // 60}:{m['duration'] % 60:02d}",
            "blue_team": blue_team,
            "red_team": red_team,
            "winner_text": winner_text,
            "objectives": {
                "blue": {"k": m["blue_kills"], "d": m["blue_dragons"], "b": m["blue_barons"],
                         "h": m["blue_heralds"], "g": m["blue_grubs"], "a": m["blue_atakhan"]},
                "red": {"k": m["red_kills"], "d": m["red_dragons"], "b": m["red_barons"],
                        "h": m["red_heralds"], "g": m["red_grubs"], "a": m["red_atakhan"]},
            }
        })

    return render_template("index.html", games=games_history)


# ---------- PLAYERS DATABASE ----------

@app.route("/players")
def players_list():
    db = get_db()
    cursor = db.cursor()

    players = []
    for role, pnames in ROSTER.items():
        for pname in pnames:
            cursor.execute("SELECT * FROM player_stats WHERE name = ?", (pname,))
            rows = cursor.fetchall()
            if not rows:
                players.append({"name": pname, "role": role, "missing": True,
                                "games": 0, "winrate": 0, "kda": 0, "champs": []})
                continue

            champs = {}
            tk = td = ta = wins = 0
            for r in rows:
                wins += 1 if r["win"] == 1 else 0
                tk += r["kills"]; td += r["deaths"]; ta += r["assists"]
                c = r["champion"]
                if c not in champs:
                    champs[c] = {"name": c, "icon": champ_icon(c), "games": 0, "wins": 0,
                                 "kills": 0, "deaths": 0, "assists": 0}
                cs = champs[c]
                cs["games"] += 1
                cs["wins"] += 1 if r["win"] == 1 else 0
                cs["kills"] += r["kills"]; cs["deaths"] += r["deaths"]; cs["assists"] += r["assists"]

            champ_list = []
            for cs in champs.values():
                cs["kda"] = round((cs["kills"] + cs["assists"]) / max(1, cs["deaths"]), 2)
                cs["wr"] = int(cs["wins"] / cs["games"] * 100) if cs["games"] else 0
                cs["kda_str"] = f"{round(cs['kills']/cs['games'],1)}/{round(cs['deaths']/cs['games'],1)}/{round(cs['assists']/cs['games'],1)}"
                champ_list.append(cs)
            champ_list.sort(key=lambda x: x["games"], reverse=True)

            games = len(rows)
            players.append({
                "name": pname, "role": role, "missing": False,
                "games": games,
                "winrate": int(wins / games * 100) if games else 0,
                "wins": wins, "losses": games - wins,
                "kda": round((tk + ta) / max(1, td), 2),
                "champs": champ_list,
            })

    return render_template("players.html", players=players)

# ---------- PLAYER DETAIL ----------

@app.route("/player/<path:player_name>")
def player_detail(player_name):
    player_name = urllib.parse.unquote(player_name)
    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
        SELECT ps.*, m.duration
        FROM player_stats ps JOIN matches m ON ps.match_id = m.id
        WHERE ps.name = ?
    """, (player_name,))
    stats = cursor.fetchall()
    if not stats:
        return f"Player '{player_name}' not found", 404

    cursor.execute("SELECT role, COUNT(*) c FROM player_stats WHERE name = ? GROUP BY role ORDER BY c DESC LIMIT 1", (player_name,))
    role_row = cursor.fetchone()
    player_role = role_row["role"] if role_row else None

    T = {"kills": 0, "deaths": 0, "assists": 0, "damage": 0, "damage_taken": 0,
         "gold": 0, "minions": 0, "wins": 0, "games": 0, "mins": 0.0,
         "vision": 0, "wards": 0, "wards_killed": 0, "control_wards": 0,
         "detector_wards": 0, "heal_shield": 0, "self_mitigated": 0,
         "cc": 0, "cc_score": 0, "time_dead": 0, "time_played": 0, "takedowns_15": 0,
         "enemy_jungle_cs": 0, "own_jungle_cs": 0, "dmg_buildings": 0, "dmg_turrets": 0,
         "dragons": 0, "barons": 0, "heralds": 0, "grubs": 0}
    champ_stats = {}

    for s in stats:
        mm = s["duration"] / 60.0 if s["duration"] else 1
        T["games"] += 1
        T["mins"] += mm
        if s["win"] == 1:
            T["wins"] += 1
        for k_src, k_dst in [("kills", "kills"), ("deaths", "deaths"), ("assists", "assists"),
                             ("damage", "damage"), ("damage_taken", "damage_taken"),
                             ("gold", "gold"), ("minions", "minions"),
                             ("vision_score", "vision"), ("wards_placed", "wards"),
                             ("wards_killed", "wards_killed"), ("control_wards", "control_wards"),
                             ("detector_wards", "detector_wards"), ("heal_shield", "heal_shield"),
                             ("self_mitigated", "self_mitigated"), ("cc_time", "cc"),
                             ("cc_score", "cc_score"), ("time_dead", "time_dead"),
                             ("time_played", "time_played"), ("takedowns_15", "takedowns_15"),
                             ("enemy_jungle_cs", "enemy_jungle_cs"), ("own_jungle_cs", "own_jungle_cs"),
                             ("dmg_buildings", "dmg_buildings"), ("dmg_turrets", "dmg_turrets"),
                             ("dragons", "dragons"), ("barons", "barons"),
                             ("heralds", "heralds"), ("grubs", "grubs")]:
            T[k_dst] += s[k_src]

        c = s["champion"]
        if c not in champ_stats:
            champ_stats[c] = {"name": c, "games": 0, "wins": 0, "kills": 0, "deaths": 0,
                              "assists": 0, "damage": 0, "gold": 0, "minions": 0,
                              "vision": 0, "mins": 0.0}
        cs = champ_stats[c]
        cs["games"] += 1
        cs["mins"] += mm
        if s["win"] == 1:
            cs["wins"] += 1
        cs["kills"] += s["kills"]; cs["deaths"] += s["deaths"]; cs["assists"] += s["assists"]
        cs["damage"] += s["damage"]; cs["gold"] += s["gold"]; cs["minions"] += s["minions"]
        cs["vision"] += s["vision_score"]

    mins = T["mins"] or 1
    g = T["games"] or 1
    T["dpm"] = int(T["damage"] / mins)
    T["gpm"] = int(T["gold"] / mins)
    T["cspm"] = round(T["minions"] / mins, 1)
    T["kda"] = round((T["kills"] + T["assists"]) / max(1, T["deaths"]), 2)
    T["winrate"] = int((T["wins"] / g) * 100)
    T["avg_vision"] = round(T["vision"] / g, 1)
    T["avg_cs"] = round(T["minions"] / g, 0)
    T["avg_kills"] = round(T["kills"] / g, 1)
    T["avg_deaths"] = round(T["deaths"] / g, 1)
    T["avg_assists"] = round(T["assists"] / g, 1)
    # New per-game averages
    T["avg_wards"] = round(T["wards"] / g, 1)
    T["avg_wards_killed"] = round(T["wards_killed"] / g, 1)
    T["avg_control_wards"] = round(T["control_wards"] / g, 1)
    T["avg_heal_shield"] = int(T["heal_shield"] / g)
    T["avg_self_mitigated"] = int(T["self_mitigated"] / g)
    T["avg_cc"] = round(T["cc"] / g, 0)
    T["avg_td15"] = round(T["takedowns_15"] / g, 1)
    T["avg_enemy_jungle"] = round(T["enemy_jungle_cs"] / g, 1)
    T["dmg_struct_pm"] = int(T["dmg_buildings"] / mins)
    T["avg_dmg_struct"] = int(T["dmg_buildings"] / g)
    # % of time spent dead
    tp = T["time_played"] or 1
    T["death_pct"] = round(T["time_dead"] / tp * 100, 1)
    T["avg_objectives"] = round((T["dragons"] + T["barons"] + T["heralds"] + T["grubs"]) / g, 1)

    # ----- Radar data (normalised 0..100 on simple scales) -----
    def cap(v, mx):
        return min(100, round(v / mx * 100)) if mx else 0
    radar = {
        "labels": ["Damage", "KDA", "Vision", "CS/min", "Objectives", "Survival"],
        "values": [
            cap(T["dpm"], 800),
            cap(T["kda"], 6),
            cap(T["avg_vision"], 60),
            cap(T["cspm"], 9),
            cap(T["avg_objectives"], 3),
            cap(100 - T["death_pct"], 100),
        ],
    }

    champ_list = []
    for c, cs in champ_stats.items():
        cm = cs["mins"] or 1
        cs["dpm"] = int(cs["damage"] / cm)
        cs["cspm"] = round(cs["minions"] / cm, 1)
        cs["wr"] = int((cs["wins"] / cs["games"]) * 100) if cs["games"] else 0
        cs["kda"] = round((cs["kills"] + cs["assists"]) / max(1, cs["deaths"]), 2)
        cs["avg_vision"] = round(cs["vision"] / cs["games"], 1) if cs["games"] else 0
        cs["icon"] = champ_icon(c)
        champ_list.append(cs)
    champ_list.sort(key=lambda x: x["games"], reverse=True)

    # match history
    cursor.execute("""
        SELECT ps.*, m.duration
        FROM player_stats ps JOIN matches m ON ps.match_id = m.id
        WHERE ps.name = ? ORDER BY m.rowid DESC
    """, (player_name,))
    history = []
    for h in cursor.fetchall():
        items = [item_icon(h[f'item{i}']) for i in range(7) if h[f'item{i}'] > 0]
        mm = h["duration"] / 60.0 if h["duration"] else 1
        history.append({
            "champion": h["champion"],
            "icon": champ_icon(h["champion"]),
            "win": h["win"],
            "kda": f"{h['kills']}/{h['deaths']}/{h['assists']}",
            "dpm": int(h["damage"] / mm) if mm else 0,
            "gpm": int(h["gold"] / mm) if mm else 0,
            "cspm": round(h["minions"] / mm, 1) if mm else 0,
            "vision": h["vision_score"],
            "duration": f"{h['duration'] // 60}:{h['duration'] % 60:02d}",
            "inventory": items,
        })

    return render_template("player_detail.html", name=player_name, role=player_role,
                           total=T, champs=champ_list, history=history, radar=radar,
                           is_support=(player_role == "UTILITY"),
                           is_jungle=(player_role == "JUNGLE"),
                           is_roster=player_name in ROSTER_NAMES)


# ---------- TEAM STATS ----------

@app.route("/team")
def team_stats():
    db = get_db()
    cursor = db.cursor()

    roster_cards = []
    for role, pnames in ROSTER.items():
        for pname in pnames:
            cursor.execute("SELECT * FROM player_stats WHERE name = ?", (pname,))
            rows = cursor.fetchall()
            if not rows:
                roster_cards.append({"role": role, "name": pname, "missing": True})
                continue
            wins = sum(1 for r in rows if r["win"] == 1)
            games = len(rows)
            k = sum(r["kills"] for r in rows)
            d = sum(r["deaths"] for r in rows)
            a = sum(r["assists"] for r in rows)
            champs = {}
            for r in rows:
                champs[r["champion"]] = champs.get(r["champion"], 0) + 1
            top_champs = sorted(champs.items(), key=lambda x: x[1], reverse=True)
            roster_cards.append({
                "role": role, "name": pname, "missing": False,
                "games": games, "wins": wins, "losses": games - wins,
                "winrate": int(wins / games * 100) if games else 0,
                "kda": round((k + a) / max(1, d), 2),
                "picks": [{"icon": champ_icon(c), "name": c, "count": n} for c, n in top_champs],
            })

    # Team objectives: only matches where at least one roster member played
    cursor.execute("""
        SELECT DISTINCT match_id, team FROM player_stats WHERE name IN ({})
    """.format(",".join("?" * len(ROSTER_NAMES))), tuple(ROSTER_NAMES))
    roster_team_in_match = {row["match_id"]: row["team"] for row in cursor.fetchall()}

    obj = {"games": 0, "wins": 0,
           "dragons": 0, "barons": 0, "heralds": 0, "grubs": 0, "atakhan": 0,
           "enemy_dragons": 0, "enemy_barons": 0, "enemy_heralds": 0, "enemy_grubs": 0}
    for match_id, team in roster_team_in_match.items():
        cursor.execute("SELECT * FROM matches WHERE id = ?", (match_id,))
        m = cursor.fetchone()
        if not m:
            continue
        obj["games"] += 1
        side = "blue" if team == "100" else "red"
        enemy = "red" if side == "blue" else "blue"
        if m["winner"] == ("Blue" if side == "blue" else "Red"):
            obj["wins"] += 1
        obj["dragons"] += m[f"{side}_dragons"]
        obj["barons"] += m[f"{side}_barons"]
        obj["heralds"] += m[f"{side}_heralds"]
        obj["grubs"] += m[f"{side}_grubs"]
        obj["atakhan"] += m[f"{side}_atakhan"]
        obj["enemy_dragons"] += m[f"{enemy}_dragons"]
        obj["enemy_barons"] += m[f"{enemy}_barons"]
        obj["enemy_heralds"] += m[f"{enemy}_heralds"]
        obj["enemy_grubs"] += m[f"{enemy}_grubs"]

    obj["winrate"] = int(obj["wins"] / obj["games"] * 100) if obj["games"] else 0

    # ---- Duo synergies: champion combinations played together by roster pairs ----
    duo_pairs = [
        ("TOP",    "JUNGLE",  "Top + Jungle"),
        ("JUNGLE", "MIDDLE",  "Jungle + Mid"),
        ("JUNGLE", "UTILITY", "Jungle + Support"),
        ("BOTTOM", "UTILITY", "Bot + Support"),
    ]
    duos = []
    for r1, r2, label in duo_pairs:
        for p1 in ROSTER.get(r1, []):
            for p2 in ROSTER.get(r2, []):
                cursor.execute("""
                    SELECT a.champion AS c1, b.champion AS c2, a.win AS w
                    FROM player_stats a
                    JOIN player_stats b ON a.match_id = b.match_id AND a.team = b.team
                    WHERE a.name = ? AND b.name = ?
                """, (p1, p2))
                combos = {}
                total_games = 0
                total_wins = 0
                for row in cursor.fetchall():
                    total_games += 1
                    if row["w"]:
                        total_wins += 1
                    key = (row["c1"], row["c2"])
                    if key not in combos:
                        combos[key] = {"c1": row["c1"], "c2": row["c2"],
                                       "icon1": champ_icon(row["c1"]),
                                       "icon2": champ_icon(row["c2"]),
                                       "games": 0, "wins": 0}
                    combos[key]["games"] += 1
                    if row["w"]:
                        combos[key]["wins"] += 1
                if total_games == 0:
                    continue  # пара ни разу не играла вместе — не показываем
                combo_list = []
                for v in combos.values():
                    v["wr"] = int(v["wins"] / v["games"] * 100) if v["games"] else 0
                    combo_list.append(v)
                combo_list.sort(key=lambda x: (-x["games"], -x["wr"]))
                duos.append({
                    "label": label,
                    "player1": p1, "player2": p2,
                    "games": total_games,
                    "wins": total_wins,
                    "winrate": int(total_wins / total_games * 100) if total_games else 0,
                    "combos": combo_list,
                })

    return render_template("team.html", roster=roster_cards, obj=obj, duos=duos)


# ---------- DRAFT BOARD ----------

@app.route("/draft")
def draft():
    db = get_db()
    cursor = db.cursor()

    # Champion pool for each roster player: games/winrate per champion
    pool = {}
    for role, pnames in ROSTER.items():
        for pname in pnames:
            cursor.execute("SELECT champion, win FROM player_stats WHERE name = ?", (pname,))
            rows = cursor.fetchall()
            champs = {}
            for r in rows:
                c = r["champion"]
                if c not in champs:
                    champs[c] = {"games": 0, "wins": 0}
                champs[c]["games"] += 1
                if r["win"] == 1:
                    champs[c]["wins"] += 1
            # ключ "ROLE — Player", чтобы у каждого игрока был свой ряд
            pool[f"{role} — {pname}"] = {
                "name": pname,
                "champs": sorted([
                    {"name": c, "icon": champ_icon(c), "games": v["games"],
                     "wins": v["wins"],
                     "wr": int(v["wins"] / v["games"] * 100) if v["games"] else 0}
                    for c, v in champs.items()
                ], key=lambda x: (-x["games"], -x["wr"]))
            }

    return render_template("draft.html", pool=pool, ddragon=DDRAGON_VER)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
