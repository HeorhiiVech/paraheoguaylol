import json
import struct
from flask import Flask, request, render_template

app = Flask(__name__)

# Актуальная версия Data Dragon для иконок
DDRAGON_VER = "16.5.1"

def extract_rofl_metadata(file_bytes: bytes) -> dict:
    try:
        if len(file_bytes) < 4:
            return {"error": "Файл слишком мал"}

        length_bytes = file_bytes[-4:]
        metadata_length = struct.unpack("<I", length_bytes)[0]

        start_index = len(file_bytes) - (metadata_length + 4)
        json_bytes = file_bytes[start_index : len(file_bytes) - 4]

        metadata_string = json_bytes.decode("utf-8")
        parsed_metadata = json.loads(metadata_string)

        if "statsJson" in parsed_metadata:
            parsed_metadata["statsJson"] = json.loads(parsed_metadata["statsJson"])
        return parsed_metadata
    except Exception as e:
        return {"error": str(e)}

@app.route("/", methods=["GET", "POST"])
def index():
    games_history = []
    # Структура: { "Имя": { "champs": { "Champion": {"wins": 0, "losses": 0, "icon": ""} }, "total_wins": 0, "total_losses": 0 } }
    player_data = {} 
    
    if request.method == "POST":
        uploaded_files = request.files.getlist("rofl_files")
        
        for file in uploaded_files:
            if not file.filename.endswith(".rofl"):
                continue
                
            file_bytes = file.read()
            metadata = extract_rofl_metadata(file_bytes)

            if "error" not in metadata:
                duration_ms = int(metadata.get("gameLength", 0))
                duration_sec = duration_ms // 1000
                duration_formatted = f"{duration_sec // 60}:{duration_sec % 60:02d}"

                stats_json = metadata.get("statsJson", [])
                blue_team, red_team = [], []
                winner_team_id = "0"

                # Определяем победившую команду
                for p in stats_json:
                    if str(p.get("WIN")) == "Win":
                        winner_team_id = str(p.get("TEAM"))
                        break

                for i, p in enumerate(stats_json):
                    # Пытаемся достать имя из разных полей, так как в реплеях NAME часто пуст
                    name = p.get("NAME") or p.get("SUMMONER_NAME")
                    if not name:
                        name = f"Player {p.get('ID') or (i + 1)}"
                    
                    champion = p.get("SKIN", "Aatrox")
                    team_id = str(p.get("TEAM"))
                    is_win = str(p.get("WIN")) == "Win"
                    icon = f"http://ddragon.leagueoflegends.com/cdn/{DDRAGON_VER}/img/champion/{champion}.png"

                    p_info = {"name": name, "champion": champion, "icon_url": icon}
                    
                    if team_id == "100":
                        blue_team.append(p_info)
                    else:
                        red_team.append(p_info)

                    # Собираем личную статистику
                    if name not in player_data:
                        player_data[name] = {"champs": {}, "total_wins": 0, "total_losses": 0}
                    
                    if champion not in player_data[name]["champs"]:
                        player_data[name]["champs"][champion] = {"wins": 0, "losses": 0, "icon": icon}
                    
                    if is_win:
                        player_data[name]["champs"][champion]["wins"] += 1
                        player_data[name]["total_wins"] += 1
                    else:
                        player_data[name]["champs"][champion]["losses"] += 1
                        player_data[name]["total_losses"] += 1

                games_history.append({
                    "duration": duration_formatted,
                    "winner": "Blue" if winner_team_id == "100" else "Red",
                    "blue_team": blue_team,
                    "red_team": red_team
                })

    # Сортируем игроков по количеству игр
    sorted_players = sorted(
        player_data.items(), 
        key=lambda x: (x[1]["total_wins"] + x[1]["total_losses"]), 
        reverse=True
    )

    return render_template("index.html", games=games_history, player_stats=sorted_players)

if __name__ == "__main__":
    app.run(debug=True, port=5000)