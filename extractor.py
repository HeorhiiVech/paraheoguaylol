import json
import struct
import os

def dump_rofl_json(file_path: str):
    if not os.path.exists(file_path):
        print(f"Ошибка: Файл '{file_path}' не найден.")
        return

    print(f"Читаем файл: {file_path}...")

    with open(file_path, "rb") as file:
        # Идем в конец файла и читаем последние 4 байта (там записана длина JSON)
        file.seek(-4, os.SEEK_END)
        length_bytes = file.read(4)
        metadata_length = struct.unpack("<I", length_bytes)[0]

        # Сдвигаемся назад на длину JSON + 4 байта и читаем сами данные
        file.seek(-(metadata_length + 4), os.SEEK_END)
        json_bytes = file.read(metadata_length)

        # Декодируем байты в текст и парсим как JSON
        metadata_string = json_bytes.decode("utf-8")
        parsed_metadata = json.loads(metadata_string)

        # Поле statsJson внутри является обычной строкой, парсим и её для удобного чтения
        if "statsJson" in parsed_metadata:
            parsed_metadata["statsJson"] = json.loads(parsed_metadata["statsJson"])

        # Сохраняем результат в новый файл
        output_file_name = file_path + ".json"
        with open(output_file_name, "w", encoding="utf-8") as out_file:
            json.dump(parsed_metadata, out_file, indent=4, ensure_ascii=False)

        print(f"Успех! Данные сохранены в файл: {output_file_name}")

if __name__ == "__main__":
    # Впиши сюда точное название своего реплея
    replay_filename = "LA2-1589553635.rofl"
    
    dump_rofl_json(replay_filename)