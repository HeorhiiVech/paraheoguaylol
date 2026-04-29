import json
import struct
from flask import Flask, request, render_template_string

app = Flask(__name__)

def extract_rofl_metadata(file_bytes: bytes) -> dict:
    if len(file_bytes) < 4:
        return {"error": "Файл слишком мал"}

    length_bytes = file_bytes[-4:]
    metadata_length = struct.unpack("<I", length_bytes)[0]

    if len(file_bytes) < metadata_length + 4:
        return {"error": "Неверный формат файла"}

    start_index = len(file_bytes) - (metadata_length + 4)
    json_bytes = file_bytes[start_index : len(file_bytes) - 4]

    metadata_string = json_bytes.decode("utf-8")
    parsed_metadata = json.loads(metadata_string)

    if "statsJson" in parsed_metadata:
        parsed_metadata["statsJson"] = json.loads(parsed_metadata["statsJson"])
    else:
        parsed_metadata["statsJson"] = []

    return parsed_metadata

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>ROFL Парсер - История Игр</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 40px; background-color: #f9f9f9; color: #333; }
        .container { max-width: 1200px; margin: auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        h1, h2 { color: #2c3e50; }
        table { border-collapse: collapse; width: 100%; margin-top: 20px; font-size: 14px; }
        th, td { border: 1px solid #ddd; padding: 12px; text-align: left; vertical-align: top; }
        th { background-color: #34495e; color: white; }
        ul { margin: 0; padding-left: 20px; }
        li { margin-bottom: 4px; }
        .blue-team { color: #2980b9; font-weight: bold; }
        .red-team { color: #c0392b; font-weight: bold; }
        .upload-form { border: 2px dashed #ccc; padding: 20px; text-align: center; border-radius: 8px; background-color: #fafafa; }
        button { background-color: #27ae60; color: white; border: none; padding: 10px 20px; cursor: pointer; border-radius: 4px; font-size: 16px; margin-top: 10px;}
        button:hover { background-color: #2ecc71; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Анализ реплеев League of Legends</h1>
        
        <div class="upload-form">
            <form action="/" method="post" enctype="multipart/form-data">
                <p>Выберите один или несколько файлов .rofl для загрузки</p>
                <input type="file" name="rofl_files" multiple accept=".rofl" required>
                <br>
                <button type="submit">Загрузить и показать стату</button>
            </form>
        </div>

        {% if games %}
        <h2>История загруженных игр</h2>
        <table>
            <thead>
                <tr>
                    <th>Версия (Патч)</th>
                    <th>Длительность</th>
                    <th>Победитель</th>
                    <th>Команда Blue</th>
                    <th>Команда Red</th>
                </tr>
            </thead>
            <tbody>
                {% for game in games %}
                <tr>
                    <td>{{ game.patch }}</td>
                    <td>{{ game.duration }}</td>
                    <td><strong>{{ game.winner }}</strong></td>
                    <td>
                        <ul>
                        {% for player in game.blue_team %}
                            <li>{{ player.name }} <span class="blue-team">({{ player.champion }})</span></li>
                        {% endfor %}
                        </ul>
                    </td>
                    <td>
                        <ul>
                        {% for player in game.red_team %}
                            <li>{{ player.name }} <span class="red-team">({{ player.champion }})</span></li>
                        {% endfor %}
                        </ul>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        {% endif %}
    </div>
</body>
</html>
"""

@app.route("/", methods=["GET", "POST"])
def index():
    games_data = []
    
    if request.method == "POST":
        uploaded_files = request.files.getlist("rofl_files")
        
        for file in uploaded_files:
            if file.filename.endswith(".rofl"):
                file_bytes = file.read()
                metadata = extract_rofl_metadata(file_bytes)

                if "error" not in metadata:
                    duration_ms = int(metadata.get("gameLength", 0))
                    duration_sec = duration_ms // 1000
                    duration_formatted = f"{duration_sec // 60}:{duration_sec % 60:02d}"

                    stats_json = metadata.get("statsJson", [])
                    blue_team = []
                    red_team = []
                    winner = "Неизвестно"

                    for player in stats_json:
                        player_info = {
                            "name": player.get("NAME", "Неизвестно"),
                            "champion": player.get("SKIN", "Неизвестно")
                        }
                        
                        if str(player.get("TEAM")) == "100":
                            blue_team.append(player_info)
                            if str(player.get("WIN")) == "Win":
                                winner = "Команда Blue"
                        else:
                            red_team.append(player_info)
                            if str(player.get("WIN")) == "Win":
                                winner = "Команда Red"

                    game_info = {
                        "patch": metadata.get("gameVersion", "Неизвестно"),
                        "duration": duration_formatted,
                        "winner": winner,
                        "blue_team": blue_team,
                        "red_team": red_team
                    }
                    games_data.append(game_info)
                else:
                    pass
            else:
                pass
    else:
        pass

    return render_template_string(HTML_TEMPLATE, games=games_data)

if __name__ == "__main__":
    app.run(debug=True, port=5000)