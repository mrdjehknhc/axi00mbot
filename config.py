import os
from dotenv import load_dotenv

load_dotenv()

# Токены AxiomTrade (обновите эти токены на актуальные)
AXIOM_ACCESS_TOKEN = os.getenv('AXIOM_ACCESS_TOKEN', "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhdXRoZW50aWNhdGVkVXNlcklkIjoiNjE4NGQ4YmMtNTllNC00ODg5LTg4MTUtYjQ4ZTViNTY5NzhkIiwiaWF0IjoxNzU1NTk3Nzg3LCJleHAiOjE3NTU1OTg3NDd9.olKxjilQi-YHW7zUO54afvupUgs6vGLgk90L5ToBLPQ")
AXIOM_REFRESH_TOKEN = os.getenv('AXIOM_REFRESH_TOKEN', "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyZWZyZXNoVG9rZW5JZCI6IjUyMmE4NWRkLTZhOWEtNGMxNC1hOTVmLWUwYTdiYmY3NDMyNSIsImlhdCI6MTc1NTU1NDg4M30.p0UG4AFjAeO_dTQzccPADHjsRhlHVdBuq0YqchzpCkQ")

# Токен Telegram бота
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в переменных окружения. Убедитесь, что файл .env существует и содержит BOT_TOKEN")

# Whitelist разрешенных пользователей (ВАЖНО: добавьте ваш User ID!)
ALLOWED_USER_IDS_STR = os.getenv('ALLOWED_USER_IDS', '')
if ALLOWED_USER_IDS_STR:
    try:
        ALLOWED_USER_IDS = [int(uid.strip()) for uid in ALLOWED_USER_IDS_STR.split(',') if uid.strip()]
    except ValueError:
        raise ValueError("ALLOWED_USER_IDS должны быть числами, разделенными запятыми. Пример: 123456789,987654321")
else:
    raise ValueError(
        "ALLOWED_USER_IDS не найден в переменных окружения!\n"
        "Добавьте в .env файл: ALLOWED_USER_IDS=ваш_telegram_user_id\n"
        "Чтобы узнать свой ID, напишите @userinfobot в Telegram"
    )

# Кошелек пользователя
WALLET_ADDRESS = os.getenv('WALLET_ADDRESS')
PRIVATE_KEY = os.getenv('PRIVATE_KEY')

if not WALLET_ADDRESS or not PRIVATE_KEY:
    raise ValueError("WALLET_ADDRESS и PRIVATE_KEY должны быть установлены в переменных окружения")

# Настройки по умолчанию
DEFAULT_SETTINGS = {
    'position_size': 10,  # Уменьшил с 100% до 10% для безопасности
    'sl': 15,
    'tp_levels': [1.5, 2, 5, 8, 10],
    'breakeven_percent': 15,
    'slippage_percent': 5.0
}

# Настройки мониторинга
PRICE_CHECK_INTERVAL = 30  # Проверка цен каждые 30 секунд
MAX_RETRIES = 3  # Максимальное количество попыток при ошибках
RETRY_DELAY = 5  # Задержка между попытками в секундах