import json
import struct
import sqlite3
import hashlib
import urllib.parse
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
                role TEXT,
                win INTEGER,
                kills INTEGER,
                deaths INTEGER,
                assists INTEGER,
                damage INTEGER,
                gold INTEGER,
                minions INTEGER,
                item0 INTEGER,
                item1 INTEGER,
                item2 INTEGER,
                item3 INTEGER,
                item4 INTEGER,
                item5 INTEGER,
                item6 INTEGER,
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
        json_bytes = file_bytes[start_index : len(file_bytes) - 4]
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

@app.route('/', methods=['GET', 'POST'])
def index():
    db = get_db()
    cursor = db.cursor()
    
    # --- БЛОК ОБРАБОТКИ ЗАГРУЗКИ ФАЙЛОВ ---
    if request.method == 'POST':
        files = request.files.getlist('files')
        for file in files:
            if file.filename == '':
                continue
            
            file_bytes = file.read()
            metadata = extract_rofl_metadata(file_bytes)
            
            if "error" in metadata:
                continue

            stats = metadata.get("statsJson")
            if not stats:
                continue

            # Генерация уникального ID матча на основе первых байт файла
            match_id = hashlib.md5(file_bytes[:1000]).hexdigest()
            duration_raw = safe_int(metadata.get("gameLength", 0))
            duration = int(duration_raw / 1000) # Перевод из мс в секунды
            
            # Проверка на дубликаты
            cursor.execute("SELECT id FROM matches WHERE id = ?", (match_id,))
            if cursor.fetchone():
                continue

            # 1. Сохранение общей информации о матче
            cursor.execute("INSERT INTO matches (id, duration) VALUES (?, ?)", (match_id, duration))

            # 2. Сохранение статистики каждого игрока
            for p in stats:
                # В реплеях победа обычно помечается строкой "Win" или "Fail"
                is_win = 1 if str(p.get("WIN", "")).lower() == "win" else 0
                
                cursor.execute('''
                    INSERT INTO player_stats (
                        match_id, name, champion, team, role, win, 
                        kills, deaths, assists, damage, gold, minions,
                        item0, item1, item2, item3, item4, item5, item6
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ''', (
                    match_id, 
                    p.get("RIOT_ID_GAME_NAME"), 
                    p.get("SKIN"), 
                    str(p.get("TEAM")), 
                    p.get("INDIVIDUAL_POSITION", "UNKNOWN"),
                    is_win, 
                    safe_int(p.get("CHAMPIONS_KILLED")), 
                    safe_int(p.get("NUM_DEATHS")), 
                    safe_int(p.get("ASSISTS")),
                    safe_int(p.get("TOTAL_DAMAGE_DEALT_TO_CHAMPIONS")), 
                    safe_int(p.get("GOLD_EARNED")),
                    safe_int(p.get("MINIONS_KILLED")) + safe_int(p.get("NEUTRAL_MINIONS_KILLED")),
                    safe_int(p.get("ITEM0")), safe_int(p.get("ITEM1")), safe_int(p.get("ITEM2")),
                    safe_int(p.get("ITEM3")), safe_int(p.get("ITEM4")), safe_int(p.get("ITEM5")), safe_int(p.get("ITEM6"))
                ))
        
        db.commit() # Фиксация изменений в scouthub.db

    # --- БЛОК ОТОБРАЖЕНИЯ ИСТОРИИ МАТЧЕЙ ---
    cursor.execute("SELECT * FROM matches ORDER BY rowid DESC LIMIT 10")
    matches_rows = cursor.fetchall()
    
    games_history = []
    for m_info in matches_rows:
        m_id = m_info["id"]
        cursor.execute("SELECT * FROM player_stats WHERE match_id = ?", (m_id,))
        participants = cursor.fetchall()
        
        match_mins = m_info["duration"] / 60.0
        
        blue_team = []
        red_team = []
        winner_text = "Unknown"
        
        for p in participants:
            inv = [f"http://ddragon.leagueoflegends.com/cdn/{DDRAGON_VER}/img/item/{p[f'item{i}']}.png" for i in range(7) if p[f'item{i}'] > 0]
            
            p_data = {
                "name": p["name"],
                "icon": f"http://ddragon.leagueoflegends.com/cdn/{DDRAGON_VER}/img/champion/{p['champion']}.png",
                "kda": f"{p['kills']}/{p['deaths']}/{p['assists']}",
                "dpm": int(p["damage"] / match_mins) if match_mins > 0 else 0,
                "win": p["win"],
                "inventory": inv,
                "team": p["team"]
            }
            
            # Логика распределения по командам (100 - Blue, 200 - Red)
            if str(p["team"]) == "100" or str(p["team"]).upper() == "BLUE":
                blue_team.append(p_data)
                if p["win"] == 1: winner_text = "Blue win"
            elif str(p["team"]) == "200" or str(p["team"]).upper() == "RED":
                red_team.append(p_data)
                if p["win"] == 1: winner_text = "Red win"
            else:
                if not blue_team or blue_team[0]["team"] == p["team"]:
                    blue_team.append(p_data)
                    if p["win"] == 1: winner_text = "Team 1 win"
                else:
                    red_team.append(p_data)
                    if p["win"] == 1: winner_text = "Team 2 win"
            
        games_history.append({
            "id": m_id,
            "duration": f"{m_info['duration'] // 60}:{m_info['duration'] % 60:02d}",
            "blue_team": blue_team,
            "red_team": red_team,
            "winner_text": winner_text
        })

    # --- БЛОК АГРЕГАЦИИ ИГРОКОВ ДЛЯ ПРАВОЙ ПАНЕЛИ ---
    cursor.execute("SELECT name, win FROM player_stats")
    all_stats = cursor.fetchall()
    player_agg = {}
    for row in all_stats:
        name = row["name"]
        if name not in player_agg:
            player_agg[name] = {"wins": 0, "count": 0}
        player_agg[name]["count"] += 1
        if row["win"] == 1: 
            player_agg[name]["wins"] += 1

    processed_players = []
    for name, d in player_agg.items():
        wr = int((d["wins"] / d["count"]) * 100) if d["count"] > 0 else 0
        processed_players.append({"name": name, "winrate": wr, "total": d["count"]})

    return render_template("index.html", games=games_history, players=processed_players)

@app.route("/players")
def players_list():
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM player_stats")
    all_rows = cursor.fetchall()
    
    roles_map = {"TOP": [], "JUNGLE": [], "MIDDLE": [], "BOTTOM": [], "UTILITY": []}
    player_agg = {}

    for row in all_rows:
        name = row["name"]
        if name not in player_agg:
            player_agg[name] = {
                "name": name, "role": row["role"], "wins": 0, "losses": 0, 
                "champs": {}, "kills": 0, "deaths": 0, "assists": 0
            }
        
        pa = player_agg[name]
        if row["win"] == 1:
            pa["wins"] = pa["wins"] + 1
        else:
            pa["losses"] = pa["losses"] + 1
        
        pa["kills"] = pa["kills"] + row["kills"]
        pa["deaths"] = pa["deaths"] + row["deaths"]
        pa["assists"] = pa["assists"] + row["assists"]
        
        c = row["champion"]
        if c not in pa["champs"]:
            pa["champs"][c] = 0
        pa["champs"][c] = pa["champs"][c] + 1

    for name, data in player_agg.items():
        total = data["wins"] + data["losses"]
        if total > 0:
            winrate = int((data["wins"] / total) * 100)
        else:
            winrate = 0
        
        sorted_champs = sorted(data["champs"].items(), key=lambda x: x[1], reverse=True)
        icons = []
        for c_name, count in sorted_champs:
            icons.append(f"http://ddragon.leagueoflegends.com/cdn/{DDRAGON_VER}/img/champion/{c_name}.png")
        
        player_card = {
            "name": name,
            "icons": icons,
            "winrate": winrate,
            "total_games": total,
            "wins": data["wins"],
            "losses": data["losses"]
        }
        
        role_key = data["role"].upper()
        if role_key in roles_map:
            roles_map[role_key].append(player_card)
        else:
            if "OTHER" not in roles_map:
                roles_map["OTHER"] = []
            roles_map["OTHER"].append(player_card)

    return render_template("players.html", roles=roles_map)

@app.route("/player/<path:player_name>")
def player_detail(player_name):
    player_name = urllib.parse.unquote(player_name)
    db = get_db()
    cursor = db.cursor()
    
    cursor.execute("""
        SELECT ps.*, m.duration 
        FROM player_stats ps 
        JOIN matches m ON ps.match_id = m.id 
        WHERE ps.name = ?
    """, (player_name,))
    stats = cursor.fetchall()
    
    if not stats:
        return f"Player '{player_name}' not found", 404

    cursor.execute("SELECT role FROM player_stats WHERE name = ? GROUP BY role ORDER BY COUNT(*) DESC LIMIT 1", (player_name,))
    role_row = cursor.fetchone()
    player_role = role_row["role"] if role_row else None

    total_stats = {"kills": 0, "deaths": 0, "assists": 0, "damage": 0, "gold": 0, "minions": 0, "wins": 0, "games": 0, "playtime_mins": 0.0}
    champ_stats = {}

    for s in stats:
        match_mins = s["duration"] / 60.0
        total_stats["games"] += 1
        total_stats["playtime_mins"] += match_mins
        if s["win"] == 1: total_stats["wins"] += 1
        total_stats["kills"] += s["kills"]
        total_stats["deaths"] += s["deaths"]
        total_stats["assists"] += s["assists"]
        total_stats["damage"] += s["damage"]
        total_stats["gold"] += s["gold"]
        total_stats["minions"] += s["minions"]
        
        c = s["champion"]
        if c not in champ_stats:
            champ_stats[c] = {"name": c, "games": 0, "wins": 0, "kills": 0, "deaths": 0, "assists": 0, "damage": 0, "gold": 0, "minions": 0, "playtime_mins": 0.0}
        cs = champ_stats[c]
        cs["games"] += 1
        cs["playtime_mins"] += match_mins
        if s["win"] == 1: cs["wins"] += 1
        cs["kills"] += s["kills"]; cs["deaths"] += s["deaths"]; cs["assists"] += s["assists"]
        cs["damage"] += s["damage"]; cs["gold"] += s["gold"]; cs["minions"] += s["minions"]

    if total_stats["playtime_mins"] > 0:
        total_stats["dpm"] = int(total_stats["damage"] / total_stats["playtime_mins"])
        total_stats["gpm"] = int(total_stats["gold"] / total_stats["playtime_mins"])
        total_stats["cspm"] = round(total_stats["minions"] / total_stats["playtime_mins"], 1)
    else:
        total_stats["dpm"] = 0; total_stats["gpm"] = 0; total_stats["cspm"] = 0.0

    champ_list = []
    for c_name, cs in champ_stats.items():
        if cs["playtime_mins"] > 0:
            cs["dpm"] = int(cs["damage"] / cs["playtime_mins"])
            cs["gpm"] = int(cs["gold"] / cs["playtime_mins"])
            cs["cspm"] = round(cs["minions"] / cs["playtime_mins"], 1)
        else:
            cs["dpm"] = 0; cs["gpm"] = 0; cs["cspm"] = 0.0
            
        # Считаем KDA и Винрейт для таблицы чемпионов
        cs["wr"] = int((cs["wins"] / cs["games"]) * 100) if cs["games"] > 0 else 0
        cs["kda"] = round((cs["kills"] + cs["assists"]) / max(1, cs["deaths"]), 2)
        cs["icon"] = f"http://ddragon.leagueoflegends.com/cdn/{DDRAGON_VER}/img/champion/{c_name}.png"
        champ_list.append(cs)
        
    # Сортируем чемпионов по количеству игр (от большего к меньшему)
    champ_list.sort(key=lambda x: x["games"], reverse=True)

    cursor.execute("""
        SELECT ps.*, m.duration, m.winner 
        FROM player_stats ps 
        JOIN matches m ON ps.match_id = m.id 
        WHERE ps.name = ? 
        ORDER BY m.rowid DESC
    """, (player_name,))
    match_history = []
    for h in cursor.fetchall():
        items = [f"http://ddragon.leagueoflegends.com/cdn/{DDRAGON_VER}/img/item/{h[f'item{i}']}.png" for i in range(7) if h[f'item{i}'] > 0]
        match_mins = h["duration"] / 60.0
        match_history.append({
            "champion": h["champion"],
            "icon": f"http://ddragon.leagueoflegends.com/cdn/{DDRAGON_VER}/img/champion/{h['champion']}.png",
            "win": h["win"],
            "kda": f"{h['kills']}/{h['deaths']}/{h['assists']}",
            "dpm": int(h["damage"] / match_mins) if match_mins > 0 else 0,
            "gpm": int(h["gold"] / match_mins) if match_mins > 0 else 0,
            "cspm": round(h["minions"] / match_mins, 1) if match_mins > 0 else 0.0,
            "duration": f"{h['duration'] // 60}:{h['duration'] % 60:02d}",
            "inventory": items
        })

    # Логика синергии
    cursor.execute("SELECT match_id, team FROM player_stats WHERE name = ?", (player_name,))
    my_matches = cursor.fetchall()
    partners = {}
    for m in my_matches:
        cursor.execute("SELECT name, win FROM player_stats WHERE match_id = ? AND team = ? AND name != ?", 
                       (m["match_id"], m["team"], player_name))
        for t in cursor.fetchall():
            p_n = t["name"]
            if p_n not in partners: partners[p_n] = {"wins": 0, "games": 0}
            partners[p_n]["games"] += 1
            if t["win"] == 1: partners[p_n]["wins"] += 1

    recommendations = []
    all_roles = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]
    
    for r_name in all_roles:
        # ПРОПУСКАЕМ РОЛЬ ТЕКУЩЕГО ИГРОКА
        if r_name == player_role:
            continue
            
        best_p = None
        reason = ""
        top_wr = -1
        
        # 1. Поиск лучшего партнера по совместным играм (Synergy)
        for p_n, p_i in partners.items():
            cursor.execute("SELECT role FROM player_stats WHERE name = ? LIMIT 1", (p_n,))
            role_row = cursor.fetchone()
            if role_row and role_row["role"] == r_name:
                wr = p_i["wins"] / p_i["games"]
                if wr > top_wr:
                    top_wr = wr
                    best_p = p_n
                    reason = f"Personal Synergy ({int(wr*100)}% Winrate together over {p_i['games']} games)"
        
        # 2. Если партнера нет, ищем глобально лучшего в этой роли
        if best_p is None:
            cursor.execute("""
                SELECT name, 
                (SUM(kills) + SUM(assists)) / CAST(MAX(1, SUM(deaths)) AS FLOAT) as kda_val,
                COUNT(*) as total_g,
                (SUM(win) * 100 / COUNT(*)) as wr_val
                FROM player_stats WHERE role = ? AND name != ? 
                GROUP BY name HAVING COUNT(*) >= 1 ORDER BY kda_val DESC LIMIT 1
            """, (r_name, player_name))
            res = cursor.fetchone()
            if res:
                best_p = res["name"]
                reason = f"Top Role Performer ({res['total_g']} games, KDA: {round(res['kda_val'], 2)}, WR: {int(res['wr_val'])}%)"

        if best_p:
            cursor.execute("SELECT champion FROM player_stats WHERE name = ? GROUP BY champion ORDER BY COUNT(*) DESC LIMIT 3", (best_p,))
            recs_icons = [f"http://ddragon.leagueoflegends.com/cdn/{DDRAGON_VER}/img/champion/{row['champion']}.png" for row in cursor.fetchall()]
            
            cursor.execute("""
                SELECT (SUM(kills)+SUM(assists))/CAST(MAX(1, SUM(deaths)) AS FLOAT) as kda, 
                (SUM(win)*100/COUNT(*)) as wr, COUNT(*) as g 
                FROM player_stats WHERE name = ?
            """, (best_p,))
            p_stats = cursor.fetchone()
            
            recommendations.append({
                "role": r_name, 
                "name": best_p, 
                "icons": recs_icons, 
                "reason": reason,
                "stats": f"{round(p_stats['kda'],1)} KDA | {p_stats['wr']}% WR | {p_stats['g']} Games"
            })

    return render_template("player_detail.html", name=player_name, total=total_stats, champs=champ_list, history=match_history, recs=recommendations)
if __name__ == "__main__":
    app.run(debug=True, port=5000)