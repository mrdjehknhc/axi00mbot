import asyncio
import logging
import signal
import sys
from concurrent.futures import ThreadPoolExecutor
from api_client import AxiomClient
from price_monitor import PriceMonitor
from bot import main as bot_main

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Глобальные переменные для корректного завершения
price_monitor = None
bot_task = None
monitor_task = None
shutdown_event = None

async def shutdown_handler():
    """Обработчик корректного завершения работы"""
    global price_monitor, bot_task, monitor_task, shutdown_event
    
    logger.info("🔄 Получен сигнал завершения, останавливаем сервисы...")
    
    # Устанавливаем событие завершения
    if shutdown_event:
        shutdown_event.set()
    
    # Останавливаем мониторинг цен
    if price_monitor:
        logger.info("📊 Останавливаем мониторинг цен...")
        await price_monitor.stop()
    
    # Останавливаем задачу мониторинга
    if monitor_task:
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            logger.info("📊 Задача мониторинга остановлена")
    
    # Останавливаем бота
    if bot_task:
        logger.info("🤖 Останавливаем Telegram бота...")
        bot_task.cancel()
        try:
            await bot_task
        except asyncio.CancelledError:
            logger.info("🤖 Telegram бот остановлен")
    
    logger.info("✅ Все сервисы остановлены")

def signal_handler(sig, frame):
    """Обработчик сигналов SIGINT и SIGTERM"""
    logger.info(f"⚡ Получен сигнал {sig}")
    # Создаем новый event loop если его нет
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(shutdown_handler())
        else:
            loop.run_until_complete(shutdown_handler())
    except RuntimeError:
        # Если event loop недоступен, создаем новый
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(shutdown_handler())
    finally:
        sys.exit(0)

async def run_price_monitoring(price_monitor):
    """Запускает мониторинг цен в отдельной корутине"""
    try:
        await price_monitor.start()
        logger.info("📊 Мониторинг цен успешно запущен")
        
        # Ждем пока мониторинг работает
        while price_monitor.is_running:
            await asyncio.sleep(1)
            
    except asyncio.CancelledError:
        logger.info("📊 Мониторинг цен был отменен")
        raise
    except Exception as e:
        logger.error(f"❌ Ошибка в мониторинге цен: {e}")
        raise

async def main():
    global price_monitor, bot_task, monitor_task, shutdown_event
    
    logger.info("🚀 Запуск торгового бота Axiom Trade v2.0 с Whitelist")
    
    # Создаем событие для координации завершения работы
    shutdown_event = asyncio.Event()
    
    try:
        # Инициализируем клиенты
        logger.info("🔧 Инициализация клиента Axiom Trade...")
        axiom_client = AxiomClient()
        
        # Проверяем подключение к Axiom API
        if not axiom_client.is_authenticated():
            logger.error("❌ Не удалось аутентифицироваться с Axiom Trade API")
            logger.error("🔑 Проверьте токены AXIOM_ACCESS_TOKEN и AXIOM_REFRESH_TOKEN в config.py")
            return
        
        logger.info("✅ Подключение к Axiom Trade API успешно")
        
        # Получаем информацию о балансе
        try:
            account_info = axiom_client.get_account_info()
            balance = account_info.get('balance', 0)
            logger.info(f"💰 Текущий баланс SOL: {balance:.6f}")
            logger.info(f"🏦 Адрес кошелька: {axiom_client.wallet_address[:8]}...{axiom_client.wallet_address[-6:]}")
            
            if balance < 0.001:
                logger.warning("⚠️ Низкий баланс! Пополните кошелек для совершения сделок")
        except Exception as e:
            logger.error(f"⚠️ Не удалось получить информацию о балансе: {e}")
        
        # Инициализируем мониторинг цен
        logger.info("📊 Инициализация системы мониторинга цен...")
        price_monitor = PriceMonitor(axiom_client, check_interval=30)  # Проверяем каждые 30 секунд
        
        # Устанавливаем обработчики сигналов для корректного завершения
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # Запускаем компоненты системы
        logger.info("🎯 Запуск основных компонентов системы...")
        
        # Создаем задачи для параллельного выполнения
        tasks = []
        
        # Задача мониторинга цен
        monitor_task = asyncio.create_task(
            run_price_monitoring(price_monitor),
            name="price_monitor"
        )
        tasks.append(monitor_task)
        
        # Задача Telegram бота
        bot_task = asyncio.create_task(
            bot_main(),
            name="telegram_bot"
        )
        tasks.append(bot_task)
        
        logger.info("🎉 Все компоненты запущены успешно!")
        logger.info("📱 Telegram бот готов к работе")
        logger.info("📊 Автоматический мониторинг позиций активен")
        logger.info("🛡️ Системы Stop Loss, Take Profit и Breakeven готовы")
        logger.info("🔒 Whitelist система активирована для безопасности")
        logger.info("\n" + "="*60)
        logger.info("🤖 ТОРГОВЫЙ БОТ AXIOM TRADE ЗАПУЩЕН")
        logger.info("🔒 РЕЖИМ БЕЗОПАСНОСТИ: WHITELIST АКТИВИРОВАН")
        logger.info("📋 Доступные функции:")
        logger.info("   • Автоматическая покупка токенов")
        logger.info("   • Stop Loss (автопродажа при убытке)")
        logger.info("   • Take Profit (частичная продажа при прибыли)")
        logger.info("   • Breakeven (перенос SL в безубыток)")
        logger.info("   • Мониторинг позиций 24/7")
        logger.info("   • Panic Sell (экстренная продажа)")
        logger.info("   • Whitelist защита от несанкционированного доступа")
        logger.info("="*60 + "\n")
        
        # Показываем информацию о whitelist
        from config import ALLOWED_USER_IDS
        logger.info(f"🔐 Разрешенные пользователи: {ALLOWED_USER_IDS}")
        logger.info("💡 Для добавления новых пользователей обновите ALLOWED_USER_IDS в .env файле")
        
        # Ждем завершения любой из задач
        done, pending = await asyncio.wait(
            tasks,
            return_when=asyncio.FIRST_COMPLETED
        )
        
        # Если одна из задач завершилась, останавливаем все остальные
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        # Проверяем исключения в завершившихся задачах
        for task in done:
            try:
                await task
            except Exception as e:
                logger.error(f"❌ Ошибка в задаче {task.get_name()}: {e}")
        
    except KeyboardInterrupt:
        logger.info("⚡ Получен сигнал прерывания (Ctrl+C)")
        await shutdown_handler()
    except Exception as e:
        logger.error(f"💥 Критическая ошибка в main(): {e}")
        logger.exception("Детали ошибки:")
        await shutdown_handler()
        raise
    finally:
        logger.info("🏁 Завершение работы торгового бота")

if __name__ == "__main__":
    try:
        # Проверяем наличие необходимых переменных окружения
        from config import BOT_TOKEN, AXIOM_ACCESS_TOKEN, AXIOM_REFRESH_TOKEN, WALLET_ADDRESS, PRIVATE_KEY, ALLOWED_USER_IDS
        
        if not BOT_TOKEN:
            print("❌ ОШИБКА: Не установлен BOT_TOKEN")
            print("📝 Создайте файл .env и добавьте BOT_TOKEN=your_bot_token")
            sys.exit(1)
            
        if not AXIOM_ACCESS_TOKEN or not AXIOM_REFRESH_TOKEN:
            print("❌ ОШИБКА: Не установлены токены Axiom Trade")
            print("📝 Обновите AXIOM_ACCESS_TOKEN и AXIOM_REFRESH_TOKEN в config.py")
            sys.exit(1)
            
        if not WALLET_ADDRESS or not PRIVATE_KEY:
            print("❌ ОШИБКА: Не установлены данные кошелька")
            print("📝 Добавьте WALLET_ADDRESS и PRIVATE_KEY в файл .env")
            sys.exit(1)
        
        if not ALLOWED_USER_IDS:
            print("❌ ОШИБКА: Не установлен whitelist пользователей")
            print("📝 Добавьте ALLOWED_USER_IDS в файл .env")
            print("💡 Чтобы узнать свой User ID, напишите @userinfobot в Telegram")
            sys.exit(1)
        
        print(f"🔐 Whitelist активирован для пользователей: {ALLOWED_USER_IDS}")
        
        # Запускаем основное приложение
        asyncio.run(main())
        
    except KeyboardInterrupt:
        logger.info("🛑 Программа завершена пользователем")
    except Exception as e:
        logger.error(f"💀 Неожиданная ошибка при запуске: {e}")
        logger.exception("Детали ошибки:")
        sys.exit(1)