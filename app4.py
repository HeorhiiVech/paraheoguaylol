import json
import struct
import sqlite3
import hashlib
from flask import Flask, request, render_template, jsonify, g

app = Flask(__name__)

DDRAGON_VER = "16.5.1"
DATABASE = 'scouthub.db'

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
                winner TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS player_stats (
                match_id TEXT,
                name TEXT,
                champion TEXT,
                team TEXT,
                win INTEGER,
                kills INTEGER,
                deaths INTEGER,
                assists INTEGER,
                damage INTEGER,
                gold INTEGER,
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

def extract_rofl_metadata(file_bytes: bytes) -> dict:
    try:
        if len(file_bytes) < 4:
            return {"error": "Файл слишком мал"}
        length_bytes = file_bytes[-4:]
        metadata_length = struct.unpack("<I", length_bytes)[0]
        start_index = len(file_bytes) - (metadata_length + 4)
        json_bytes = file_bytes[start_index : len(file_bytes) - 4]
        parsed_metadata = json.loads(json_bytes.decode("utf-8"))
        if "statsJson" in parsed_metadata:
            parsed_metadata["statsJson"] = json.loads(parsed_metadata["statsJson"])
        return parsed_metadata
    except Exception as e:
        return {"error": str(e)}

def safe_int(value) -> int:
    try:
        return int(value)
    except (ValueError, TypeError):
        return 0

@app.route("/", methods=["GET", "POST"])
def index():
    db = get_db()
    cursor = db.cursor()

    if request.method == "POST":
        uploaded_files = request.files.getlist("rofl_files")
        for file in uploaded_files:
            if not file.filename.endswith(".rofl"):
                continue
            file_bytes = file.read()
            metadata = extract_rofl_metadata(file_bytes)

            if "error" not in metadata:
                duration_sec = int(metadata.get("gameLength", 0)) // 1000
                stats_json = metadata.get("statsJson", [])
                players_str = "".join([p.get("NAME", "") or p.get("SUMMONER_NAME", "") for p in stats_json])
                match_id = hashlib.md5(f"{duration_sec}_{players_str}".encode()).hexdigest()

                cursor.execute("SELECT id FROM matches WHERE id = ?", (match_id,))
                if cursor.fetchone():
                    continue

                winner_team_id = "0"
                for p in stats_json:
                    if str(p.get("WIN")) == "Win":
                        winner_team_id = str(p.get("TEAM"))
                        break

                winner_str = "Blue" if winner_team_id == "100" else "Red"
                cursor.execute("INSERT INTO matches (id, duration, winner) VALUES (?, ?, ?)", (match_id, duration_sec, winner_str))

                for i, p in enumerate(stats_json):
                    name = p.get("NAME") or p.get("SUMMONER_NAME") or f"Player {i+1}"
                    champion = p.get("SKIN", "Aatrox")
                    team_id = "Blue" if str(p.get("TEAM")) == "100" else "Red"
                    win = 1 if str(p.get("WIN")) == "Win" else 0
                    
                    cursor.execute('''
                        INSERT INTO player_stats (match_id, name, champion, team, win, kills, deaths, assists, damage, gold)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (match_id, name, champion, team_id, win, 
                          safe_int(p.get("CHAMPIONS_KILLED")), safe_int(p.get("NUM_DEATHS")), 
                          safe_int(p.get("ASSISTS")), safe_int(p.get("TOTAL_DAMAGE_DEALT_TO_CHAMPIONS")), 
                          safe_int(p.get("GOLD_EARNED")))
                    )
                    cursor.execute("INSERT OR IGNORE INTO player_profiles (name) VALUES (?)", (name,))
        db.commit()

    # --- СБОР ИСТОРИИ МАТЧЕЙ ---
    cursor.execute("SELECT * FROM matches ORDER BY ROWID DESC")
    matches_rows = cursor.fetchall()
    games_history = []
    
    for m_row in matches_rows:
        m_id = m_row["id"]
        cursor.execute("SELECT * FROM player_stats WHERE match_id = ?", (m_id,))
        p_stats = cursor.fetchall()
        
        blue_team = []
        red_team = []
        for ps in p_stats:
            p_info = {
                "name": ps["name"],
                "champion": ps["champion"],
                "icon_url": f"http://ddragon.leagueoflegends.com/cdn/{DDRAGON_VER}/img/champion/{ps['champion']}.png",
                "kda_str": f"{ps['kills']}/{ps['deaths']}/{ps['assists']}"
            }
            if ps["team"] == "Blue":
                blue_team.append(p_info)
            else:
                red_team.append(p_info)
            
        games_history.append({
            "duration": f"{m_row['duration'] // 60}:{m_row['duration'] % 60:02d}",
            "winner": m_row["winner"],
            "blue_team": blue_team,
            "red_team": red_team
        })

    # --- СБОР СТАТИСТИКИ ИГРОКОВ ---
    cursor.execute("SELECT * FROM player_stats")
    all_stats = cursor.fetchall()
    player_data = {}
    synergy_map = {}
    
    matches_to_players = {}
    for row in all_stats:
        m_id = row["match_id"]
        team = row["team"]
        name = row["name"]
        if m_id not in matches_to_players:
            matches_to_players[m_id] = {"Blue": [], "Red": []}
        matches_to_players[m_id][team].append(name)

    # Расчет синергии
    for m_id, teams in matches_to_players.items():
        for team_name, players in teams.items():
            is_win = 0
            for r in all_stats:
                if r["match_id"] == m_id and r["name"] == players[0]:
                    is_win = r["win"]
                    break
            for i in range(len(players)):
                for j in range(len(players)):
                    if i == j:
                        continue
                    p1, p2 = players[i], players[j]
                    if p1 not in synergy_map:
                        synergy_map[p1] = {}
                    if p2 not in synergy_map[p1]:
                        synergy_map[p1][p2] = {"games": 0, "wins": 0}
                    synergy_map[p1][p2]["games"] += 1
                    if is_win:
                        synergy_map[p1][p2]["wins"] += 1

    cursor.execute("SELECT id, duration FROM matches")
    durations = {r["id"]: r["duration"]/60.0 for r in cursor.fetchall()}
    
    for row in all_stats:
        name = row["name"]
        if name not in player_data:
            player_data[name] = {
                "champs": {}, 
                "wins": 0, 
                "losses": 0, 
                "kills": 0, 
                "deaths": 0, 
                "assists": 0, 
                "damage": 0, 
                "gold": 0, 
                "playtime": 0.0
            }
        
        d = player_data[name]
        if row["win"]:
            d["wins"] += 1
        else:
            d["losses"] += 1
            
        d["kills"] += row["kills"]
        d["deaths"] += row["deaths"]
        d["assists"] += row["assists"]
        d["damage"] += row["damage"]
        d["gold"] += row["gold"]
        d["playtime"] += durations.get(row["match_id"], 0)
        
        champ_name = row["champion"]
        if champ_name not in d["champs"]:
            d["champs"][champ_name] = {
                "wins": 0, 
                "losses": 0, 
                "icon": f"http://ddragon.leagueoflegends.com/cdn/{DDRAGON_VER}/img/champion/{champ_name}.png"
            }
        
        if row["win"]:
            d["champs"][champ_name]["wins"] += 1
        else:
            d["champs"][champ_name]["losses"] += 1

    # Сбор профилей (звезды/заметки)
    cursor.execute("SELECT * FROM player_profiles")
    profiles = {r["name"]: r for r in cursor.fetchall()}

    processed_players = []
    for name, d in player_data.items():
        total_games = d["wins"] + d["losses"]
        kda_ratio = (d["kills"] + d["assists"]) / d["deaths"] if d["deaths"] > 0 else (d["kills"] + d["assists"])
        
        best_partner_str = "Нет данных"
        if name in synergy_map:
            partners = sorted(synergy_map[name].items(), key=lambda x: (x[1]["wins"]/x[1]["games"], x[1]["games"]), reverse=True)
            if partners:
                p_name, s = partners[0]
                win_rate = int(s['wins'] / s['games'] * 100)
                best_partner_str = f"{p_name} ({win_rate}% WR, {s['games']} games)"

        prof = profiles.get(name, {"is_starred": 0, "note": ""})
        processed_players.append({
            "name": name, 
            "total_games": total_games, 
            "wins": d["wins"], 
            "losses": d["losses"],
            "champs": d["champs"], 
            "kda_str": f"{d['kills']/total_games:.1f}/{d['deaths']/total_games:.1f}/{d['assists']/total_games:.1f}",
            "kda_ratio": round(kda_ratio, 2), 
            "dpm": int(d["damage"]/d["playtime"]) if d["playtime"] > 0 else 0, 
            "gpm": int(d["gold"]/d["playtime"]) if d["playtime"] > 0 else 0,
            "is_starred": prof["is_starred"], 
            "note": prof["note"], 
            "synergy": best_partner_str
        })

    sorted_players = sorted(processed_players, key=lambda x: (x["is_starred"], x["total_games"]), reverse=True)
    top_5_kda = sorted(processed_players, key=lambda x: x["kda_ratio"], reverse=True)[:5]
    
    return render_template("index.html", player_stats=sorted_players, top_5=top_5_kda, games=games_history)

@app.route("/api/update_profile", methods=["POST"])
def update_profile():
    data = request.json
    db = get_db()
    if data.get("is_starred") is not None:
        db.execute("UPDATE player_profiles SET is_starred = ? WHERE name = ?", (data["is_starred"], data["name"]))
    if data.get("note") is not None:
        db.execute("UPDATE player_profiles SET note = ? WHERE name = ?", (data["note"], data["name"]))
    db.commit()
    return jsonify({"status": "success"})

if __name__ == "__main__":
    app.run(debug=True, port=5000)