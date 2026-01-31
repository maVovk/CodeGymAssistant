# ! Весь текст ниже АИ слоп, я его не читал, а просто удалить пока было лень)

# Google Sheets Manager

Асинхронная Python библиотека для управления Google Spreadsheets с встроенным кэшированием. Идеально подходит для автоматической отметки выполнения заданий командами в Google таблицах.

## Особенности

- ✅ **Асинхронная работа** - все операции полностью асинхронные для максимальной производительности
- 🚀 **Умное кэширование** - структура таблицы (команды и упражнения) кэшируется с настраиваемым TTL
- 🔒 **Service Account аутентификация** - безопасная серверная аутентификация
- 🎯 **Простой API** - методы для check/uncheck/get статуса задания
- 💾 **Возврат предыдущих значений** - все операции записи возвращают предыдущее значение ячейки
- 🔄 **Исправление ошибок** - метод uncheck для отмены неправильных отметок
- 💪 **Обработка ошибок** - детальные исключения для всех случаев
- 📦 **Готов к использованию** - можно импортировать как библиотеку
- 🌐 **Multi-user safe** - не кэширует значения ячеек, только структуру

## Требования

- Python 3.8+
- Google Cloud проект с включенным Google Sheets API
- Service Account credentials (JSON файл)

## Установка

1. Клонируйте репозиторий:
```bash
git clone <repository-url>
cd CodeGymAssistant
```

2. Установите зависимости:
```bash
pip install -r requirements.txt
```

3. Настройте переменные окружения:
```bash
cp .env.example .env
# Отредактируйте .env и укажите путь к вашему service account файлу
```

## Настройка Google Service Account

### 1. Создайте проект в Google Cloud Console

1. Перейдите на [Google Cloud Console](https://console.cloud.google.com/)
2. Создайте новый проект или выберите существующий
3. Запишите Project ID

### 2. Включите Google Sheets API

1. В консоли перейдите в **APIs & Services** → **Library**
2. Найдите **Google Sheets API**
3. Нажмите **Enable**

### 3. Создайте Service Account

1. Перейдите в **APIs & Services** → **Credentials**
2. Нажмите **Create Credentials** → **Service Account**
3. Заполните имя и описание
4. Нажмите **Create and Continue**
5. (Опционально) Назначьте роли
6. Нажмите **Done**

### 4. Создайте ключ

1. Найдите созданный Service Account в списке
2. Нажмите на него
3. Перейдите на вкладку **Keys**
4. Нажмите **Add Key** → **Create new key**
5. Выберите **JSON**
6. Сохраните скачанный файл в безопасном месте

### 5. Дайте доступ к таблице

1. Откройте ваш Google Spreadsheet
2. Нажмите **Share** (Поделиться)
3. Добавьте email вашего Service Account (найдете в JSON файле, поле `client_email`)
4. Дайте права **Editor** (Редактор)

## Быстрый старт

### Базовое использование

```python
import asyncio
from sheets_manager import SheetsManager

async def main():
    # Инициализация менеджера
    manager = SheetsManager(
        service_account_file="path/to/credentials.json",
        cache_ttl=3600  # Кэш на 1 час (только для структуры)
    )
    
    # Обязательно вызвать перед использованием
    await manager.initialize()
    
    # Отметить задание для команды (возвращает предыдущее значение)
    previous = await manager.check_team_exercise(
        spreadsheet_id="1abc123def456...",
        team_name="Team Alpha",
        exercise_name="Exercise 1"
    )
    
    print(f"✓ Задание отмечено! Предыдущее значение: {previous}")
    
    # Снять отметку (для исправления ошибок)
    previous = await manager.uncheck_team_exercise(
        spreadsheet_id="1abc123def456...",
        team_name="Team Alpha",
        exercise_name="Exercise 1"
    )
    
    print(f"✓ Отметка снята! Предыдущее значение: {previous}")

if __name__ == "__main__":
    asyncio.run(main())
```

### Использование с .env файлом

```python
import asyncio
import os
from dotenv import load_dotenv
from sheets_manager import SheetsManager

load_dotenv()

async def main():
    manager = SheetsManager(
        service_account_file=os.getenv('GOOGLE_SERVICE_ACCOUNT_FILE'),
        cache_ttl=int(os.getenv('CACHE_TTL_SECONDS', '3600'))
    )
    await manager.initialize()
    
    await manager.check_team_exercise(
        spreadsheet_id="YOUR_SPREADSHEET_ID",
        team_name="Team Alpha",
        exercise_name="Exercise 1"
    )

asyncio.run(main())
```

## Структура таблицы

Таблица должна иметь следующую структуру:

```
     A          |    B        |    C        |    D        | ...
-----------------------------------------------------------------
1              | Exercise 1  | Exercise 2  | Exercise 3  | ...
2  Team Alpha  |             |             |             |
3  Team Beta   |             |             |             |
4  Team Gamma  |             |             |             |
```

- **Колонка A** (с 2-й строки): Названия команд
- **Строка 1** (с колонки B): Названия заданий
- **Ячейки пересечения**: Чекбоксы для отметки выполнения (TRUE/FALSE)

## API Документация

### SheetsManager

Основной класс для работы с Google Sheets.

#### `__init__(service_account_file, cache_ttl)`

Инициализирует менеджер.

**Параметры:**
- `service_account_file` (str, optional): Путь к JSON файлу с credentials
- `cache_ttl` (int): Время жизни кэша в секундах (по умолчанию: 3600)

#### `async initialize()`

Инициализирует асинхронный клиент. **Обязательно** вызвать перед использованием других методов.

**Raises:**
- `AuthenticationError`: Если не удалось аутентифицироваться

#### `async check_team_exercise(spreadsheet_id, team_name, exercise_name)`

Отмечает задание как выполненное для указанной команды (устанавливает TRUE).

**Параметры:**
- `spreadsheet_id` (str): ID таблицы Google Sheets
- `team_name` (str): Название команды (из колонки A)
- `exercise_name` (str): Название задания (из строки 1)

**Returns:**
- `Any`: Предыдущее значение ячейки до обновления

**Raises:**
- `ValueError`: Некорректные входные данные
- `TeamNotFoundException`: Команда не найдена
- `ExerciseNotFoundException`: Задание не найдено
- `GoogleSheetsAPIError`: Ошибка API
- `RuntimeError`: Менеджер не инициализирован

**Пример:**
```python
previous = await manager.check_team_exercise(
    spreadsheet_id="1abc123...",
    team_name="Team Alpha",
    exercise_name="Exercise 1"
)
print(f"Предыдущее значение: {previous}")
```

#### `async uncheck_team_exercise(spreadsheet_id, team_name, exercise_name)`

Снимает отметку с задания (устанавливает FALSE). Полезно для исправления ошибок.

**Параметры:**
- `spreadsheet_id` (str): ID таблицы Google Sheets
- `team_name` (str): Название команды (из колонки A)
- `exercise_name` (str): Название задания (из строки 1)

**Returns:**
- `Any`: Предыдущее значение ячейки до обновления

**Raises:**
- `ValueError`: Некорректные входные данные
- `TeamNotFoundException`: Команда не найдена
- `ExerciseNotFoundException`: Задание не найдено
- `GoogleSheetsAPIError`: Ошибка API
- `RuntimeError`: Менеджер не инициализирован

**Пример:**
```python
previous = await manager.uncheck_team_exercise(
    spreadsheet_id="1abc123...",
    team_name="Team Alpha",
    exercise_name="Exercise 1"
)
print(f"Отметка снята. Было: {previous}")
```

#### `async get_team_exercise_status(spreadsheet_id, team_name, exercise_name)`

Получает текущий статус задания без изменения.

**Параметры:**
- `spreadsheet_id` (str): ID таблицы Google Sheets
- `team_name` (str): Название команды (из колонки A)
- `exercise_name` (str): Название задания (из строки 1)

**Returns:**
- `Any`: Текущее значение ячейки

**Raises:**
- `ValueError`: Некорректные входные данные
- `TeamNotFoundException`: Команда не найдена
- `ExerciseNotFoundException`: Задание не найдено
- `GoogleSheetsAPIError`: Ошибка API
- `RuntimeError`: Менеджер не инициализирован

**Пример:**
```python
status = await manager.get_team_exercise_status(
    spreadsheet_id="1abc123...",
    team_name="Team Alpha",
    exercise_name="Exercise 1"
)
print(f"Текущий статус: {status}")
```

#### `async get_teams(spreadsheet_id)`

Получает список всех команд из таблицы. Полезно для валидации при старте приложения.

**Параметры:**
- `spreadsheet_id` (str): ID таблицы Google Sheets

**Returns:**
- `List[str]`: Список названий команд из колонки A

**Raises:**
- `ValueError`: Некорректный spreadsheet_id
- `GoogleSheetsAPIError`: Ошибка API
- `RuntimeError`: Менеджер не инициализирован

**Пример:**
```python
teams = await manager.get_teams("1abc123...")
print(f"Доступные команды: {', '.join(teams)}")
```

#### `async get_exercises(spreadsheet_id)`

Получает список всех упражнений из таблицы. Полезно для валидации при старте приложения.

**Параметры:**
- `spreadsheet_id` (str): ID таблицы Google Sheets

**Returns:**
- `List[str]`: Список названий упражнений из строки 1

**Raises:**
- `ValueError`: Некорректный spreadsheet_id
- `GoogleSheetsAPIError`: Ошибка API
- `RuntimeError`: Менеджер не инициализирован

**Пример:**
```python
exercises = await manager.get_exercises("1abc123...")
print(f"Доступные упражнения: {', '.join(exercises)}")
```

#### `async invalidate_cache(spreadsheet_id)`

Сбрасывает кэш для конкретной таблицы.

**Параметры:**
- `spreadsheet_id` (str): ID таблицы

**Пример:**
```python
await manager.invalidate_cache("1abc123...")
```

#### `async clear_all_cache()`

Очищает весь кэш.

**Пример:**
```python
await manager.clear_all_cache()
```

#### `get_cache_stats()`

Возвращает статистику кэша.

**Returns:**
- `dict`: Словарь со статистикой

**Пример:**
```python
stats = manager.get_cache_stats()
print(f"Cached spreadsheets: {stats['total_cached']}")
```

## Обработка ошибок

Библиотека предоставляет специализированные исключения:

### `TeamNotFoundException`

Выбрасывается, когда команда не найдена в колонке A.

```python
from sheets_manager import TeamNotFoundException

try:
    await manager.check_team_exercise(...)
except TeamNotFoundException as e:
    print(f"Команда не найдена: {e.team_name}")
```

### `ExerciseNotFoundException`

Выбрасывается, когда задание не найдено в строке 1.

```python
from sheets_manager import ExerciseNotFoundException

try:
    await manager.check_team_exercise(...)
except ExerciseNotFoundException as e:
    print(f"Задание не найдено: {e.exercise_name}")
```

### `GoogleSheetsAPIError`

Выбрасывается при ошибках Google Sheets API.

```python
from sheets_manager import GoogleSheetsAPIError

try:
    await manager.check_team_exercise(...)
except GoogleSheetsAPIError as e:
    print(f"Ошибка API: {e}")
    print(f"Оригинальная ошибка: {e.original_error}")
```

### `AuthenticationError`

Выбрасывается при проблемах с аутентификацией.

```python
from sheets_manager import AuthenticationError

try:
    await manager.initialize()
except AuthenticationError as e:
    print(f"Ошибка аутентификации: {e}")
```

## Продвинутое использование

### Параллельная обработка нескольких команд

```python
import asyncio
from sheets_manager import SheetsManager

async def check_multiple_teams():
    manager = SheetsManager("credentials.json")
    await manager.initialize()
    
    tasks = [
        manager.check_team_exercise("1abc...", "Team Alpha", "Exercise 1"),
        manager.check_team_exercise("1abc...", "Team Beta", "Exercise 1"),
        manager.check_team_exercise("1abc...", "Team Gamma", "Exercise 2"),
    ]
    
    # Выполнить все параллельно
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            print(f"Задача {i} упала: {result}")
        else:
            print(f"Задача {i} успешна")

asyncio.run(check_multiple_teams())
```

### Работа с несколькими таблицами

```python
async def manage_multiple_spreadsheets():
    manager = SheetsManager("credentials.json", cache_ttl=7200)
    await manager.initialize()
    
    spreadsheets = [
        "1abc123...",
        "2def456...",
        "3ghi789...",
    ]
    
    for spreadsheet_id in spreadsheets:
        await manager.check_team_exercise(
            spreadsheet_id=spreadsheet_id,
            team_name="Team Alpha",
            exercise_name="Exercise 1"
        )
    
    # Статистика кэша покажет все 3 таблицы
    print(manager.get_cache_stats())
```

### Настройка времени жизни кэша

```python
# Короткий кэш для часто изменяющихся таблиц
manager = SheetsManager("credentials.json", cache_ttl=300)  # 5 минут

# Длинный кэш для стабильных таблиц
manager = SheetsManager("credentials.json", cache_ttl=7200)  # 2 часа

# Отключить кэш (не рекомендуется)
manager = SheetsManager("credentials.json", cache_ttl=0)
```

## Оптимизация производительности

### 1. Используйте кэширование структуры

Кэширование структуры включено по умолчанию и значительно снижает количество API запросов:
- Первый запрос к таблице: загружает структуру (названия команд и упражнений)
- Последующие запросы: используют кэш структуры
- Значения ячеек НЕ кэшируются - всегда актуальные данные для multi-user сценариев
- Кэш структуры автоматически истекает по TTL

### 2. Батч-операции

Для множественных операций используйте `asyncio.gather()`:

```python
tasks = [manager.check_team_exercise(...) for ...]
await asyncio.gather(*tasks)
```

### 3. Оптимальный TTL

Выберите TTL в зависимости от частоты изменений **структуры** таблицы (добавление/удаление команд или упражнений):
- Структура меняется редко (стабильная команда/задания): 3600-7200 секунд
- Структура меняется часто (динамические команды): 300-600 секунд
- Структура меняется постоянно: используйте `invalidate_cache()` при изменениях

**Важно:** TTL влияет только на кэш структуры (названия команд и упражнений), а не на значения ячеек. Значения всегда читаются свежими из API.

## Лимиты Google Sheets API

Google Sheets API имеет квоты:
- **100 запросов в 100 секунд на пользователя**
- **500 запросов в 100 секунд на проект**

Библиотека минимизирует количество запросов через:
- Кэширование структуры таблицы (команды и упражнения)
- Батч-чтение структуры
- Два запроса на операцию записи (чтение предыдущего значения + запись нового)
- Один запрос на операцию чтения статуса

**Примечание:** Операции `check_team_exercise` и `uncheck_team_exercise` делают 2 API запроса (read + write), чтобы вернуть предыдущее значение. Если вам не нужно предыдущее значение, используйте `get_team_exercise_status` (1 запрос).

## Troubleshooting

### Ошибка: "Spreadsheet not found"

**Причина:** Service Account не имеет доступа к таблице.

**Решение:** Откройте таблицу и дайте доступ email'у из `client_email` в JSON файле.

### Ошибка: "Authentication error"

**Причина:** Некорректный путь к credentials файлу или неверный формат.

**Решение:** 
- Проверьте путь к файлу
- Убедитесь, что файл в формате JSON
- Проверьте, что Service Account активен

### Ошибка: "Team/Exercise not found"

**Причина:** Название команды или задания не совпадает с данными в таблице.

**Решение:**
- Проверьте точное написание (регистр не важен)
- Убедитесь, что команда в колонке A (с 2-й строки)
- Убедитесь, что задание в строке 1 (с колонки B)

### Медленная работа

**Причина:** Кэш отключен или TTL слишком мал.

**Решение:**
- Увеличьте `cache_ttl`
- Проверьте скорость интернета
- Используйте параллельные операции с `asyncio.gather()`

## Примеры

Полные примеры использования смотрите в файле [`example.py`](example.py):

- Базовое использование
- Обработка нескольких команд
- Параллельное выполнение
- Работа с кэшем
- Обработка ошибок

Запуск примеров:
```bash
python example.py
```

## Структура проекта

```
CodeGymAssistant/
├── sheets_manager/
│   ├── __init__.py          # Публичный API
│   ├── manager.py           # Основной класс SheetsManager
│   ├── cache.py             # Система кэширования
│   ├── utils.py             # Вспомогательные функции
│   └── exceptions.py        # Кастомные исключения
├── .env.example             # Пример конфигурации
├── requirements.txt         # Зависимости
├── example.py               # Примеры использования
└── README.md                # Документация
```

## Лицензия

MIT License

## Поддержка

При возникновении проблем:
1. Проверьте раздел [Troubleshooting](#troubleshooting)
2. Изучите [примеры](example.py)
3. Проверьте настройки Service Account

---

**Примечание:** Этот проект предназначен для использования в качестве библиотеки. Импортируйте `SheetsManager` в свой код для интеграции с Google Sheets.
