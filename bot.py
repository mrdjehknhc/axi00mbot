import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery
import asyncio
import re

from config import BOT_TOKEN, DEFAULT_SETTINGS, ALLOWED_USER_IDS
from api_client import AxiomClient
from storage import PositionStorage
from middleware import WhitelistMiddleware  # Импортируем наш middleware

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
storage_fsm = MemoryStorage()
dp = Dispatcher(storage=storage_fsm)

# 🔒 РЕГИСТРИРУЕМ WHITELIST MIDDLEWARE (ВАЖНО: это должно быть в начале!)
whitelist_middleware = WhitelistMiddleware()
dp.message.middleware(whitelist_middleware)
dp.callback_query.middleware(whitelist_middleware)

logger.info(f"🔒 Whitelist активирован для пользователей: {ALLOWED_USER_IDS}")

# Инициализация клиентов
axiom_client = AxiomClient()

# Состояния для FSM
class TradeStates(StatesGroup):
    awaiting_contract = State()

class SettingsStates(StatesGroup):
    setting_position_size = State()
    setting_sl = State()
    setting_tp = State()
    setting_breakeven = State()
    setting_slippage = State()  # Новое состояние для слиппеджа

# Глобальные переменные
user_settings = {}

# Клавиатуры
def main_keyboard():
    keyboard = [
        [types.InlineKeyboardButton(text="💰 Купить токен", callback_data='buy_token')],
        [types.InlineKeyboardButton(text="📊 Мои сделки", callback_data='my_trades')],
        [types.InlineKeyboardButton(text="⚙️ Настройки", callback_data='settings')],
        [types.InlineKeyboardButton(text="💼 Баланс", callback_data='balance')]
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)

def back_to_menu_keyboard():
    keyboard = [[types.InlineKeyboardButton(text="🔙 Назад", callback_data='back_to_menu')]]
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)

def settings_keyboard():
    keyboard = [
        [types.InlineKeyboardButton(text="💰 Размер позиции", callback_data='set_position_size')],
        [types.InlineKeyboardButton(text="🛑 Стоп-лосс (%)", callback_data='set_sl')],
        [types.InlineKeyboardButton(text="🎯 Уровни TP", callback_data='set_tp')],
        [types.InlineKeyboardButton(text="⚖️ Безубыток (%)", callback_data='set_breakeven')],
        [types.InlineKeyboardButton(text="📊 Слиппедж (%)", callback_data='set_slippage')],  # Новая кнопка
        [types.InlineKeyboardButton(text="🔙 Назад", callback_data='back_to_menu')]
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)

def trades_keyboard(positions):
    keyboard = []
    for pos in positions:
        contract_short = f"{pos['contract_address'][:6]}...{pos['contract_address'][-4:]}"
        pnl = pos.get('pnl', 0)
        pnl_icon = "🟢" if pnl >= 0 else "🔴"
        
        keyboard.append([
            types.InlineKeyboardButton(
                text=f"{pnl_icon} {contract_short} ({pnl:+.1f}%)", 
                callback_data=f'position_details_{pos["contract_address"]}'
            )
        ])
        keyboard.append([
            types.InlineKeyboardButton(
                text=f"🚨 Panic Sell {contract_short}", 
                callback_data=f'panic_sell_{pos["contract_address"]}'
            )
        ])
    
    keyboard.append([types.InlineKeyboardButton(text="🔄 Обновить", callback_data='my_trades')])
    keyboard.append([types.InlineKeyboardButton(text="🔙 Назад", callback_data='back_to_menu')])
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)

def position_details_keyboard(contract_address):
    keyboard = [
        [types.InlineKeyboardButton(
            text="🚨 Panic Sell", 
            callback_data=f'panic_sell_{contract_address}'
        )],
        [types.InlineKeyboardButton(
            text="📈 Продать 25%", 
            callback_data=f'partial_sell_25_{contract_address}'
        )],
        [types.InlineKeyboardButton(
            text="📈 Продать 50%", 
            callback_data=f'partial_sell_50_{contract_address}'
        )],
        [types.InlineKeyboardButton(text="🔙 К сделкам", callback_data='my_trades')]
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)

# Функции для форматирования TP
def format_tp_levels(tp_levels):
    """Форматируем список TP для отображения"""
    if not tp_levels:
        return "Не установлены"
    
    # Проверяем старый формат (список чисел) и конвертируем в новый
    if tp_levels and isinstance(tp_levels[0], (int, float)):
        # Конвертируем старый формат в новый с 25% объёмом по умолчанию
        tp_levels = [{'level': level, 'volume_percent': 25} for level in tp_levels]
    
    formatted = []
    for tp in tp_levels:
        level = tp.get('level', 0)
        volume = tp.get('volume_percent', 0)
        formatted.append(f"{level}x ({volume}%)")
    
    return ", ".join(formatted)

def parse_tp_input(text):
    """
    Парсим ввод пользователя для TP с толерантностью к пробелам
    Формат: "1.5:25,2:30,3:45" 
    Поддерживает пробелы: "1.5 : 25 , 2 : 30 , 3 : 45"
    """
    tp_levels = []
    
    # Нормализуем ввод - убираем все лишние пробелы вокруг разделителей
    # Сначала убираем пробелы вокруг двоеточий и запятых
    normalized_text = text.strip()
    
    # Убираем пробелы вокруг запятых
    normalized_text = ','.join(part.strip() for part in normalized_text.split(','))
    
    # Теперь убираем пробелы вокруг двоеточий в каждой части
    pairs = []
    for pair in normalized_text.split(','):
        if ':' in pair:
            level_part, volume_part = pair.split(':', 1)  # Ограничиваем split одним разделением
            normalized_pair = f"{level_part.strip()}:{volume_part.strip()}"
            pairs.append(normalized_pair)
        else:
            pairs.append(pair.strip())
    
    for pair in pairs:
        if not pair:  # Пропускаем пустые строки
            continue
            
        try:
            if ':' in pair:
                level_str, volume_str = pair.split(':', 1)
                level_str = level_str.strip()
                volume_str = volume_str.strip()
                
                if not level_str or not volume_str:
                    raise ValueError(f"Пустое значение уровня или объёма")
                
                level = float(level_str)
                volume = float(volume_str)
                
                if level <= 1:
                    raise ValueError(f"Уровень {level} должен быть больше 1 (например: 1.5 = +50% прибыли)")
                if volume <= 0 or volume > 100:
                    raise ValueError(f"Объём {volume}% должен быть от 1 до 100")
                
                tp_levels.append({
                    'level': level,
                    'volume_percent': volume
                })
            else:
                raise ValueError(f"Отсутствует разделитель ':' между уровнем и объёмом")
                
        except ValueError as e:
            # Более детальное сообщение об ошибке с примерами
            raise ValueError(
                f"Ошибка в '{pair}': {str(e)}\n\n"
                f"💡 Правильные примеры:\n"
                f"• 1.5:25 (при +50% прибыли продать 25%)\n"
                f"• 2:30,3:20 (при +100% продать 30%, при +200% продать 20%)\n"
                f"• 1.5 : 25 , 2 : 30 (пробелы разрешены)"
            )
    
    if not tp_levels:
        raise ValueError(
            "Не найдено ни одного валидного TP уровня\n\n"
            "💡 Примеры правильного формата:\n"
            "• 1.5:25\n"
            "• 2:30,3:20\n"
            "• 1.5 : 25 , 2 : 30"
        )
    
    # Проверяем общий объём
    total_volume = sum(tp['volume_percent'] for tp in tp_levels)
    if total_volume > 100:
        levels_info = ', '.join([f"{tp['level']}x:{tp['volume_percent']}%" for tp in tp_levels])
        raise ValueError(
            f"Общий объём TP ({total_volume}%) превышает 100%\n\n"
            f"📊 Ваши уровни: {levels_info}\n"
            f"💡 Уменьшите проценты так, чтобы сумма была ≤ 100%"
        )
    
    return tp_levels

# Обработчики команд
@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    
    # Персонализированное приветствие для авторизованных пользователей
    user_name = message.from_user.first_name or "пользователь"
    
    welcome_text = f"""
🤖 Добро пожаловать, {user_name}!
Торговый бот Axiom Trade готов к работе.

🔒 Система безопасности: ✅ Активна
👤 Ваш ID: {message.from_user.id}

С помощью этого бота вы можете:
• 💰 Покупать мемкоины на SOL
• 🎯 Настраивать автоматические TP/SL уровни
• ⚖️ Автоматическое перемещение в безубыток
• 📊 Управлять открытыми позициями
• ⚙️ Настраивать параметры торговли

🔥 Автоматические функции:
• Stop Loss: автоматическая продажа при убытке
• Take Profit: гибкая частичная продажа при прибыли
• Breakeven: перенос SL в безубыток

Выберите действие из меню ниже:
"""
    await message.answer(welcome_text, reply_markup=main_keyboard())

@dp.message(Command("cancel"))
async def cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Операция отменена.",
        reply_markup=back_to_menu_keyboard()
    )

# Обработчики callback-запросов
@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu_handler(callback_query: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback_query.answer()
    
    user_name = callback_query.from_user.first_name or "пользователь"
    
    welcome_text = f"""
🤖 Главное меню торгового бота Axiom Trade

Привет, {user_name}! 👋

С помощью этого бота вы можете:
• 💰 Покупать мемкоины на SOL
• 🎯 Настраивать автоматические TP/SL уровни
• ⚖️ Автоматическое перемещение в безубыток
• 📊 Управлять открытыми позициями
• ⚙️ Настраивать параметры торговли

Выберите действие из меню ниже:
"""
    await callback_query.message.edit_text(welcome_text, reply_markup=main_keyboard())

@dp.callback_query(F.data == "balance")
async def show_balance(callback_query: CallbackQuery):
    await callback_query.answer()
    
    try:
        # Проверяем аутентификацию
        if not axiom_client.is_authenticated():
            await callback_query.message.edit_text(
                "❌ Ошибка аутентификации с Axiom Trade API.\n"
                "Проверьте токены в конфигурации.",
                reply_markup=back_to_menu_keyboard()
            )
            return
        
        account_info = axiom_client.get_account_info()
        balance = account_info.get('balance', 0)
        
        text = f"""
💼 Информация о балансе:

💰 SOL: {balance:.6f}
🌐 Кошелек: {axiom_client.wallet_address[:8]}...{axiom_client.wallet_address[-6:]}

ℹ️ Состояние API: ✅ Подключен
"""
        
        await callback_query.message.edit_text(
            text,
            reply_markup=back_to_menu_keyboard()
        )
            
    except Exception as e:
        logger.error(f"Ошибка получения баланса: {e}")
        await callback_query.message.edit_text(
            f"❌ Ошибка получения баланса: {str(e)}",
            reply_markup=back_to_menu_keyboard()
        )

@dp.callback_query(F.data == "buy_token")
async def buy_token(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await state.set_state(TradeStates.awaiting_contract)
    await callback_query.message.edit_text(
        "🔍 Введите адрес контракта SOL токена:\n\n"
        "Пример: So11111111111111111111111111111111111111112\n\n"
        "⚠️ Убедитесь, что адрес правильный!",
        reply_markup=back_to_menu_keyboard()
    )

@dp.callback_query(F.data == "my_trades")
async def show_my_trades(callback_query: CallbackQuery):
    await callback_query.answer()
    
    try:
        positions = axiom_client.get_user_positions(callback_query.from_user.id)
        
        if not positions:
            await callback_query.message.edit_text(
                "🔭 У вас нет активных позиций.\n\n"
                "Откройте позицию через меню 'Купить токен'",
                reply_markup=back_to_menu_keyboard()
            )
            return
            
        text = "📊 Ваши активные позиции:\n\n"
        
        total_invested = 0
        total_current_value = 0
        
        for position in positions:
            contract_address = position.get('contract_address', 'N/A')
            invested = position.get('invested_sol', 0)
            token_amount = position.get('token_amount', 0)
            entry_price = position.get('entry_price', 0)
            current_price = position.get('current_price', entry_price)
            pnl = position.get('pnl', 0)
            sl = position.get('sl', 0)
            breakeven_moved = position.get('breakeven_moved', False)
            tp_executed = position.get('tp_executed', [])
            tp_levels = position.get('tp_levels', [])
            
            current_value = token_amount * current_price if current_price > 0 else invested
            total_invested += invested
            total_current_value += current_value
            
            status_icon = "🟢" if pnl >= 0 else "🔴"
            breakeven_status = " 🔒" if breakeven_moved else ""
            tp_status = f" TP:{len(tp_executed)}/{len(tp_levels)}" if tp_levels else ""
            
            text += f"{status_icon} {contract_address[:8]}...{contract_address[-6:]}\n"
            text += f"   💰 Вложено: {invested:.4f} SOL\n"
            text += f"   📈 Текущая стоимость: {current_value:.4f} SOL\n"
            text += f"   📊 PnL: {pnl:+.2f}%{breakeven_status}{tp_status}\n"
            text += f"   🛑 SL: {sl}%\n\n"
        
        total_pnl = ((total_current_value - total_invested) / total_invested * 100) if total_invested > 0 else 0
        total_icon = "🟢" if total_pnl >= 0 else "🔴"
        
        text += f"💼 Общий портфель:\n"
        text += f"   Вложено: {total_invested:.4f} SOL\n"
        text += f"   Текущая стоимость: {total_current_value:.4f} SOL\n"
        text += f"   {total_icon} Общий PnL: {total_pnl:+.2f}%"
        
        await callback_query.message.edit_text(
            text,
            reply_markup=trades_keyboard(positions)
        )
            
    except Exception as e:
        logger.error(f"Ошибка получения сделок: {e}")
        await callback_query.message.edit_text(
            f"❌ Ошибка получения сделок: {str(e)}",
            reply_markup=back_to_menu_keyboard()
        )

@dp.callback_query(F.data == "settings")
async def show_settings(callback_query: CallbackQuery):
    await callback_query.answer()
    
    user_id = callback_query.from_user.id
    settings = user_settings.get(user_id, DEFAULT_SETTINGS)
    
    text = f"""
⚙️ Текущие настройки:

💰 Размер позиции: {settings['position_size']}% от баланса
🛑 Стоп-лосс: {settings['sl']}%
🎯 Уровни тейк-профита: {format_tp_levels(settings['tp_levels'])}
⚖️ Перемещение в безубыток: {settings['breakeven_percent']}%
📊 Слиппедж: {settings['slippage_percent']}%

ℹ️ Автоматические функции работают 24/7:
• SL: продажа всей позиции при убытке {settings['sl']}%
• TP: продажа указанного объёма на каждом уровне прибыли
• Breakeven: перенос SL в 0% при прибыли {settings['breakeven_percent']}%
• Slippage: максимальное отклонение цены {settings['slippage_percent']}%

Выберите параметр для изменения:
"""
    await callback_query.message.edit_text(text, reply_markup=settings_keyboard())

# Обработчики настроек
@dp.callback_query(F.data == "set_position_size")
async def set_position_size(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await state.set_state(SettingsStates.setting_position_size)
    await callback_query.message.edit_text(
        "💰 Введите новый размер позиции (в % от баланса):\n\n"
        "Примеры: 10, 25, 50\n"
        "⚠️ Рекомендуется не более 20% для безопасности",
        reply_markup=back_to_menu_keyboard()
    )

@dp.callback_query(F.data == "set_sl")
async def set_sl(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await state.set_state(SettingsStates.setting_sl)
    await callback_query.message.edit_text(
        "🛑 Введите новый стоп-лосс (в %):\n\n"
        "Примеры: 10, 15, 20\n"
        "⚠️ При достижении этого убытка позиция будет автоматически продана",
        reply_markup=back_to_menu_keyboard()
    )

@dp.callback_query(F.data == "set_tp")
async def set_tp(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await state.set_state(SettingsStates.setting_tp)
    await callback_query.message.edit_text(
        "🎯 Настройка тейк-профитов:\n\n"
        "Формат: уровень:объём,уровень:объём...\n"
        "Пример: 1.5:25,2:30,3:20,6:25\n\n"
        "Где:\n"
        "• 1.5 = +50% прибыли, продать 25% позиции\n"
        "• 2 = +100% прибыли, продать 30% позиции\n"
        "• 3 = +200% прибыли, продать 20% позиции\n"
        "• 6 = +500% прибыли, продать 25% позиции\n\n"
        "⚠️ Общий объём не должен превышать 100%\n"
        "💡 Пробелы разрешены: 1.5 : 25 , 2 : 30",
        reply_markup=back_to_menu_keyboard()
    )

@dp.callback_query(F.data == "set_breakeven")
async def set_breakeven(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await state.set_state(SettingsStates.setting_breakeven)
    await callback_query.message.edit_text(
        "⚖️ Введите новый процент для перемещения в безубыток:\n\n"
        "Примеры: 10, 15, 20\n"
        "ℹ️ При достижении этой прибыли стоп-лосс переместится в точку входа (0%)",
        reply_markup=back_to_menu_keyboard()
    )

@dp.callback_query(F.data == "set_slippage")
async def set_slippage(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await state.set_state(SettingsStates.setting_slippage)
    
    current_settings = user_settings.get(callback_query.from_user.id, DEFAULT_SETTINGS)
    current_slippage = current_settings['slippage_percent']
    
    await callback_query.message.edit_text(
        f"📊 Настройка слиппеджа\n\n"
        f"Текущее значение: {current_slippage}%\n\n"
        f"Введите новое значение слиппеджа (в %):\n\n"
        f"💡 Рекомендации:\n"
        f"• 0.1-1% - стабильные токены\n"
        f"• 1-5% - обычные мемкоины\n"
        f"• 5-15% - волатильные мемкоины\n"
        f"• 15-50% - экстремально волатильные\n\n"
        f"⚠️ Низкий слиппедж = транзакции могут не проходить\n"
        f"⚠️ Высокий слиппедж = больше потерь при покупке/продаже",
        reply_markup=back_to_menu_keyboard()
    )

# Обработчик деталей позиции
@dp.callback_query(F.data.startswith("position_details_"))
async def show_position_details(callback_query: CallbackQuery):
    await callback_query.answer()
    
    contract_address = callback_query.data.replace('position_details_', '')
    
    try:
        positions = axiom_client.get_user_positions(callback_query.from_user.id)
        position = next((p for p in positions if p['contract_address'] == contract_address), None)
        
        if not position:
            await callback_query.message.edit_text(
                "❌ Позиция не найдена",
                reply_markup=back_to_menu_keyboard()
            )
            return
        
        # Получаем актуальную информацию
        current_price = axiom_client.get_token_price(contract_address)
        if current_price > 0:
            entry_price = position.get('entry_price', 0)
            pnl = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
        else:
            pnl = position.get('pnl', 0)
        
        invested = position.get('invested_sol', 0)
        token_amount = position.get('token_amount', 0)
        entry_price = position.get('entry_price', 0)
        sl = position.get('sl', 0)
        tp_levels = position.get('tp_levels', [])
        breakeven_percent = position.get('breakeven_percent', 0)
        breakeven_moved = position.get('breakeven_moved', False)
        tp_executed = position.get('tp_executed', [])
        tx_hash = position.get('transaction_hash', 'N/A')
        slippage = position.get('slippage_percent', DEFAULT_SETTINGS['slippage_percent'])  # Показываем слиппедж
        
        current_value = token_amount * current_price if current_price > 0 else invested
        status_icon = "🟢" if pnl >= 0 else "🔴"
        
        text = f"""
📊 Детали позиции:

🔄 Контракт: {contract_address[:8]}...{contract_address[-6:]}
{status_icon} PnL: {pnl:+.2f}%

💰 Финансы:
   Вложено: {invested:.6f} SOL
   Токенов: {token_amount:.2f}
   Текущая стоимость: {current_value:.6f} SOL

📈 Цены:
   Цена входа: {entry_price:.8f} SOL
   Текущая цена: {current_price:.8f} SOL

🎯 Настройки:
   🛑 Stop Loss: {sl}%
   🎯 Take Profit: {format_tp_levels(tp_levels)}
   ⚖️ Breakeven: {breakeven_percent}%
   📊 Slippage: {slippage}%

📊 Статус:
   Безубыток: {'✅ Активирован' if breakeven_moved else '❌ Не активирован'}
   TP выполнено: {len(tp_executed)}/{len(tp_levels)}

🔗 Транзакция: {tx_hash[:8]}...
"""
        
        await callback_query.message.edit_text(
            text,
            reply_markup=position_details_keyboard(contract_address)
        )
        
    except Exception as e:
        logger.error(f"Ошибка получения деталей позиции: {e}")
        await callback_query.message.edit_text(
            f"❌ Ошибка получения деталей позиции: {str(e)}",
            reply_markup=back_to_menu_keyboard()
        )

# Обработчик частичной продажи
@dp.callback_query(F.data.startswith("partial_sell_"))
async def partial_sell(callback_query: CallbackQuery):
    await callback_query.answer()
    
    # Извлекаем процент и адрес контракта
    data_parts = callback_query.data.split('_')
    percentage = float(data_parts[2])
    contract_address = '_'.join(data_parts[3:])
    
    try:
        processing_msg = await callback_query.message.edit_text("🔄 Выполняем частичную продажу...")
        
        result = axiom_client.close_position(callback_query.from_user.id, contract_address, percentage)
        
        if result.get('success') or result.get('signature'):
            await processing_msg.edit_text(
                f"✅ Продано {percentage}% позиции!\n"
                f"Контракт: {contract_address[:8]}...{contract_address[-6:]}\n"
                f"Транзакция: {result.get('signature', 'N/A')[:8]}...",
                reply_markup=back_to_menu_keyboard()
            )
        else:
            await processing_msg.edit_text(
                f"❌ Ошибка частичной продажи: {result.get('error', 'Unknown error')}",
                reply_markup=back_to_menu_keyboard()
            )
            
    except Exception as e:
        logger.error(f"Ошибка частичной продажи: {e}")
        await callback_query.message.edit_text(
            f"❌ Ошибка частичной продажи: {str(e)}",
            reply_markup=back_to_menu_keyboard()
        )

# Обработчик panic sell
@dp.callback_query(F.data.startswith("panic_sell_"))
async def panic_sell(callback_query: CallbackQuery):
    await callback_query.answer()
    
    contract_address = callback_query.data.replace('panic_sell_', '')
    
    try:
        processing_msg = await callback_query.message.edit_text("🚨 Выполняем экстренную продажу...")
        
        result = axiom_client.close_position(callback_query.from_user.id, contract_address, 100.0)
        
        if result.get('success') or result.get('signature'):
            await processing_msg.edit_text(
                f"✅ Позиция полностью закрыта!\n"
                f"Контракт: {contract_address[:8]}...{contract_address[-6:]}\n"
                f"Транзакция: {result.get('signature', 'N/A')[:8]}...",
                reply_markup=back_to_menu_keyboard()
            )
        else:
            await processing_msg.edit_text(
                f"❌ Ошибка закрытия позиции: {result.get('error', 'Unknown error')}",
                reply_markup=back_to_menu_keyboard()
            )
            
    except Exception as e:
        logger.error(f"Ошибка закрытия позиции: {e}")
        await callback_query.message.edit_text(
            f"❌ Ошибка закрытия позиции: {str(e)}",
            reply_markup=back_to_menu_keyboard()
        )

# Обработчики текстовых сообщений в состояниях
@dp.message(TradeStates.awaiting_contract)
async def handle_contract_address(message: types.Message, state: FSMContext):
    contract_address = message.text.strip()
    
    # Валидация SOL адреса (base58, длина 32-44 символа)
    if len(contract_address) < 32 or len(contract_address) > 44:
        await message.answer(
            "❌ Неверный формат адреса контракта SOL.\n"
            "Адрес должен содержать от 32 до 44 символов.",
            reply_markup=back_to_menu_keyboard()
        )
        await state.clear()
        return
    
    # Получаем настройки пользователя
    user_id = message.from_user.id
    settings = user_settings.get(user_id, DEFAULT_SETTINGS)
    
    try:
        # Проверяем аутентификацию
        if not axiom_client.is_authenticated():
            await message.answer(
                "❌ Ошибка аутентификации с Axiom Trade API.\n"
                "Проверьте токены в конфигурации.",
                reply_markup=back_to_menu_keyboard()
            )
            await state.clear()
            return
            
        # Получаем баланс
        account_info = axiom_client.get_account_info()
        balance = account_info.get('balance', 0)
        
        if balance <= 0:
            await message.answer(
                "❌ Недостаточно SOL на балансе.",
                reply_markup=back_to_menu_keyboard()
            )
            await state.clear()
            return
            
        # Рассчитываем размер позиции
        amount = (balance * settings['position_size'] / 100)
        
        if amount < 0.001:  # Минимальный размер позиции
            await message.answer(
                "❌ Размер позиции слишком мал (минимум 0.001 SOL).\n"
                "Увеличьте размер позиции в настройках или пополните баланс.",
                reply_markup=back_to_menu_keyboard()
            )
            await state.clear()
            return
        
        # Отправляем сообщение о начале покупки с информацией о слиппедже
        tp_summary = format_tp_levels(settings['tp_levels'])
        processing_msg = await message.answer(
            f"🔄 Открываю позицию...\n\n"
            f"💰 Размер: {amount:.4f} SOL\n"
            f"🛑 Stop Loss: {settings['sl']}%\n"
            f"🎯 Take Profit: {tp_summary}\n"
            f"⚖️ Breakeven: {settings['breakeven_percent']}%\n"
            f"📊 Слиппедж: {settings['slippage_percent']}%"
        )
        
        # Открываем позицию с пользовательскими настройками слиппеджа
        position = axiom_client.open_position(
            user_id=user_id,
            contract_address=contract_address,
            amount=amount,
            sl=settings['sl'],
            tp=settings['tp_levels'],
            breakeven=settings['breakeven_percent'],
            slippage=settings['slippage_percent']  # Передаём пользовательский слиппедж
        )
        
        await processing_msg.edit_text(
            f"✅ Позиция открыта!\n\n"
            f"🔄 Контракт: {contract_address[:8]}...{contract_address[-6:]}\n"
            f"💰 Размер: {amount:.4f} SOL\n"
            f"💸 Токенов получено: {position.get('token_amount', 0):.2f}\n"
            f"📈 Цена входа: {position.get('entry_price', 0):.8f} SOL\n"
            f"🛑 SL: {settings['sl']}%\n"
            f"🎯 TP: {tp_summary}\n"
            f"⚖️ Безубыток: {settings['breakeven_percent']}%\n"
            f"📊 Слиппедж: {settings['slippage_percent']}%\n"
            f"🔗 Транзакция: {position.get('transaction_hash', 'N/A')[:8]}...\n\n"
            f"🤖 Автоматический мониторинг активирован!",
            reply_markup=back_to_menu_keyboard()
        )
        
    except Exception as e:
        logger.error(f"Ошибка открытия позиции: {e}")
        await message.answer(
            f"❌ Ошибка открытия позиции:\n{str(e)}",
            reply_markup=back_to_menu_keyboard()
        )
    
    await state.clear()

@dp.message(SettingsStates.setting_position_size)
async def handle_position_size(message: types.Message, state: FSMContext):
    try:
        size = float(message.text)
        if 0 < size <= 100:
            user_id = message.from_user.id
            if user_id not in user_settings:
                user_settings[user_id] = DEFAULT_SETTINGS.copy()
            user_settings[user_id]['position_size'] = size
            
            await message.answer(
                f"✅ Размер позиции установлен: {size}%",
                reply_markup=back_to_menu_keyboard()
            )
        else:
            await message.answer(
                "❌ Размер позиции должен быть между 0 и 100%",
                reply_markup=back_to_menu_keyboard()
            )
    except ValueError:
        await message.answer(
            "❌ Пожалуйста, введите число",
            reply_markup=back_to_menu_keyboard()
        )
    await state.clear()

@dp.message(SettingsStates.setting_sl)
async def handle_sl(message: types.Message, state: FSMContext):
    try:
        sl = float(message.text)
        if sl > 0:
            user_id = message.from_user.id
            if user_id not in user_settings:
                user_settings[user_id] = DEFAULT_SETTINGS.copy()
            user_settings[user_id]['sl'] = sl
            
            await message.answer(
                f"✅ Стоп-лосс установлен: {sl}%",
                reply_markup=back_to_menu_keyboard()
            )
        else:
            await message.answer(
                "❌ Стоп-лосс должен быть положительным числом",
                reply_markup=back_to_menu_keyboard()
            )
    except ValueError:
        await message.answer(
            "❌ Пожалуйста, введите число",
            reply_markup=back_to_menu_keyboard()
        )
    await state.clear()

@dp.message(SettingsStates.setting_tp)
async def handle_tp(message: types.Message, state: FSMContext):
    try:
        tp_levels = parse_tp_input(message.text)
        
        user_id = message.from_user.id
        if user_id not in user_settings:
            user_settings[user_id] = DEFAULT_SETTINGS.copy()
        user_settings[user_id]['tp_levels'] = tp_levels
        
        total_volume = sum(tp['volume_percent'] for tp in tp_levels)
        
        await message.answer(
            f"✅ Уровни TP установлены:\n{format_tp_levels(tp_levels)}\n\n"
            f"📊 Общий объём: {total_volume}%",
            reply_markup=back_to_menu_keyboard()
        )
        
    except ValueError as e:
        await message.answer(
            f"❌ Ошибка в формате TP:\n{str(e)}",
            reply_markup=back_to_menu_keyboard()
        )
    await state.clear()

@dp.message(SettingsStates.setting_breakeven)
async def handle_breakeven(message: types.Message, state: FSMContext):
    try:
        breakeven = float(message.text)
        if breakeven > 0:
            user_id = message.from_user.id
            if user_id not in user_settings:
                user_settings[user_id] = DEFAULT_SETTINGS.copy()
            user_settings[user_id]['breakeven_percent'] = breakeven
            
            await message.answer(
                f"✅ Безубыток установлен: {breakeven}%",
                reply_markup=back_to_menu_keyboard()
            )
        else:
            await message.answer(
                "❌ Процент безубытка должен быть положительным числом",
                reply_markup=back_to_menu_keyboard()
            )
    except ValueError:
        await message.answer(
            "❌ Пожалуйста, введите число",
            reply_markup=back_to_menu_keyboard()
        )
    await state.clear()

@dp.message(SettingsStates.setting_slippage)
async def handle_slippage(message: types.Message, state: FSMContext):
    try:
        slippage = float(message.text.strip())
        
        # Валидация слиппеджа
        if slippage <= 0:
            await message.answer(
                "❌ Слиппедж должен быть положительным числом\n\n"
                "💡 Попробуйте: 5 (для 5% слиппеджа)",
                reply_markup=back_to_menu_keyboard()
            )
            await state.clear()
            return
        
        if slippage > 99:
            await message.answer(
                "❌ Слиппедж не может быть больше 99%\n\n"
                "⚠️ Такой высокий слиппедж приведет к огромным потерям",
                reply_markup=back_to_menu_keyboard()
            )
            await state.clear()
            return
        
        # Предупреждения для экстремальных значений
        warning_text = ""
        if slippage < 0.5:
            warning_text = "\n⚠️ Низкий слиппедж - транзакции могут не проходить в периоды высокой активности"
        elif slippage > 20:
            warning_text = "\n⚠️ Высокий слиппедж - возможны значительные потери при торговле"
        
        # Сохраняем настройку
        user_id = message.from_user.id
        if user_id not in user_settings:
            user_settings[user_id] = DEFAULT_SETTINGS.copy()
        user_settings[user_id]['slippage_percent'] = slippage
        
        await message.answer(
            f"✅ Слиппедж установлен: {slippage}%{warning_text}",
            reply_markup=back_to_menu_keyboard()
        )
        
    except ValueError:
        await message.answer(
            "❌ Пожалуйста, введите корректное число\n\n"
            "💡 Примеры: 5, 10, 15.5",
            reply_markup=back_to_menu_keyboard()
        )
    
    await state.clear()

async def main():
    logger.info("🚀 Бот запущен с активным whitelist")
    try:
        await dp.start_polling(bot, skip_updates=True)
    except Exception as e:
        logger.error(f"Ошибка запуска бота: {e}")

if __name__ == "__main__":
    asyncio.run(main())