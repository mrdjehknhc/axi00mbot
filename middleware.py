import logging
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update, Message, CallbackQuery
from typing import Callable, Dict, Any, Awaitable
from config import ALLOWED_USER_IDS

logger = logging.getLogger(__name__)

class WhitelistMiddleware(BaseMiddleware):
    """
    Middleware для проверки whitelist пользователей
    Блокирует доступ всем пользователям, кроме разрешенных
    """
    
    def __init__(self):
        self.allowed_users = set(ALLOWED_USER_IDS)
        logger.info(f"🔒 Whitelist инициализован для {len(self.allowed_users)} пользователей: {list(self.allowed_users)}")
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        
        user = None
        user_id = None
        
        # Извлекаем информацию о пользователе из разных типов событий
        if isinstance(event, Update):
            if event.message:
                user = event.message.from_user
                user_id = user.id if user else None
            elif event.callback_query:
                user = event.callback_query.from_user
                user_id = user.id if user else None
        elif hasattr(event, 'from_user') and event.from_user:
            user = event.from_user
            user_id = user.id
        
        # Если не удалось определить пользователя, блокируем
        if not user_id:
            logger.warning("🚫 Попытка доступа без идентификации пользователя")
            return
        
        # Проверяем whitelist
        if user_id not in self.allowed_users:
            username = user.username if user and user.username else "неизвестно"
            full_name = f"{user.first_name} {user.last_name or ''}".strip() if user else "неизвестно"
            
            logger.warning(
                f"🚫 ЗАБЛОКИРОВАННАЯ ПОПЫТКА ДОСТУПА:\n"
                f"   👤 User ID: {user_id}\n"
                f"   📝 Username: @{username}\n"
                f"   🏷️ Имя: {full_name}\n"
                f"   📅 Время: {logger.handlers[0].formatter.formatTime() if logger.handlers else 'н/д'}"
            )
            
            # Отправляем сообщение о блокировке
            await self._send_access_denied_message(event, user_id, username)
            return  # Блокируем выполнение handler
        
        # Логируем разрешенный доступ
        if user:
            username = user.username if user.username else "без username"
            logger.debug(f"✅ Разрешен доступ пользователю {user_id} (@{username})")
        
        # Продолжаем выполнение handler для разрешенных пользователей
        return await handler(event, data)
    
    async def _send_access_denied_message(self, event: TelegramObject, user_id: int, username: str):
        """Отправляем красивое сообщение об отказе в доступе"""
        try:
            message_text = (
                "🚫 **Доступ запрещен**\n\n"
                "⚠️ Этот бот предназначен только для авторизованных пользователей.\n\n"
                f"🆔 Ваш ID: `{user_id}`\n"
                f"👤 Username: @{username}\n\n"
                "📞 Если вы считаете, что это ошибка, обратитесь к администратору бота."
            )
            
            bot = None
            
            # Получаем объект бота из события
            if isinstance(event, Update):
                if event.message:
                    bot = event.message.bot
                    await bot.send_message(
                        chat_id=user_id,
                        text=message_text,
                        parse_mode="Markdown"
                    )
                elif event.callback_query:
                    bot = event.callback_query.bot
                    await bot.send_message(
                        chat_id=user_id,
                        text=message_text,
                        parse_mode="Markdown"
                    )
                    # Отвечаем на callback query, чтобы убрать "загрузку"
                    await event.callback_query.answer("🚫 Доступ запрещен")
            elif hasattr(event, 'bot'):
                bot = event.bot
                await bot.send_message(
                    chat_id=user_id,
                    text=message_text,
                    parse_mode="Markdown"
                )
            
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения об отказе в доступе: {e}")
    
    def add_user(self, user_id: int):
        """Добавить пользователя в whitelist (программно)"""
        self.allowed_users.add(user_id)
        logger.info(f"➕ Пользователь {user_id} добавлен в whitelist")
    
    def remove_user(self, user_id: int):
        """Удалить пользователя из whitelist (программно)"""
        if user_id in self.allowed_users:
            self.allowed_users.remove(user_id)
            logger.info(f"➖ Пользователь {user_id} удален из whitelist")
    
    def is_allowed(self, user_id: int) -> bool:
        """Проверить, разрешен ли доступ пользователю"""
        return user_id in self.allowed_users
    
    def get_allowed_users(self) -> set:
        """Получить список разрешенных пользователей"""
        return self.allowed_users.copy()