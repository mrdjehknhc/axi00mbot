import time
import asyncio
import aiohttp
from typing import Dict
import logging

logger = logging.getLogger(__name__)

class PriceMonitor:
    def __init__(self, axiom_client, check_interval: int = 30):
        self.axiom_client = axiom_client
        self.storage = axiom_client.storage
        self.check_interval = check_interval
        self.is_running = False
        self.session = None
        self.monitoring_task = None
    
    async def get_token_price(self, contract_address: str) -> float:
        """Получаем текущую цену токена через Jupiter API"""
        try:
            if not self.session:
                self.session = aiohttp.ClientSession()
            
            url = f"https://quote-api.jup.ag/v6/price?ids={contract_address}"
            async with self.session.get(url, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    if 'data' in data and contract_address in data['data']:
                        return float(data['data'][contract_address]['price'])
                else:
                    logger.warning(f"Jupiter API returned status {response.status} for {contract_address}")
                    return 0.0
        except asyncio.TimeoutError:
            logger.warning(f"Timeout getting price for {contract_address}")
            return 0.0
        except Exception as e:
            logger.error(f"Ошибка получения цены токена {contract_address}: {e}")
            return 0.0
    
    async def check_prices(self):
        """Проверяем цены всех отслеживаемых позиций"""
        while self.is_running:
            try:
                positions_data = self.storage.load_positions()
                
                for user_id_str, positions in positions_data.items():
                    user_id = int(user_id_str)
                    
                    # Создаем копию списка для безопасной итерации
                    positions_copy = positions.copy()
                    
                    for position in positions_copy:
                        contract_address = position.get('contract_address')
                        if not contract_address:
                            continue
                        
                        # Получаем текущую цену
                        current_price = await self.get_token_price(contract_address)
                        
                        if current_price > 0:
                            entry_price = position.get('entry_price', 0)
                            if entry_price > 0:
                                # Рассчитываем PnL в процентах
                                pnl_percent = ((current_price - entry_price) / entry_price) * 100
                                
                                # Обновляем текущую цену и PnL в позиции
                                self.storage.update_position(
                                    user_id,
                                    position['id'],
                                    {
                                        'current_price': current_price, 
                                        'pnl': pnl_percent
                                    }
                                )
                                
                                logger.debug(f"Updated price for {contract_address[:8]}...: {current_price:.8f}, PnL: {pnl_percent:.2f}%")
                                
                                # Проверяем условия для автоматических действий
                                await self.check_automation_triggers(user_id, position, current_price, pnl_percent)
                
                await asyncio.sleep(self.check_interval)
                
            except Exception as e:
                logger.error(f"Ошибка в мониторинге цен: {e}")
                await asyncio.sleep(self.check_interval)
    
    async def check_automation_triggers(self, user_id: int, position: Dict, current_price: float, pnl_percent: float):
        """Проверяем условия для автоматического выполнения SL/TP/Breakeven с новой логикой TP"""
        try:
            position_id = position['id']
            contract_address = position['contract_address']
            sl = position.get('sl', 15)
            tp_levels = position.get('tp_levels', [])
            breakeven_percent = position.get('breakeven_percent', 15)
            breakeven_moved = position.get('breakeven_moved', False)
            tp_executed = position.get('tp_executed', [])
            
            # 1. Проверяем стоп-лосс
            if pnl_percent <= -sl:
                logger.warning(f"🛑 STOP LOSS triggered for {contract_address[:8]}...: {pnl_percent:.2f}% <= -{sl}%")
                success = self.axiom_client.execute_stop_loss(user_id, position)
                if success:
                    logger.info(f"✅ Stop Loss executed successfully for {contract_address[:8]}...")
                else:
                    logger.error(f"❌ Stop Loss execution failed for {contract_address[:8]}...")
                return  # После SL больше ничего не проверяем
            
            # 2. Проверяем перемещение в безубыток
            if pnl_percent >= breakeven_percent and not breakeven_moved:
                logger.info(f"⚖️ Moving to breakeven for {contract_address[:8]}...: PnL {pnl_percent:.2f}% >= {breakeven_percent}%")
                success = self.axiom_client.move_to_breakeven(user_id, position)
                if success:
                    logger.info(f"✅ Moved to breakeven for {contract_address[:8]}...")
                else:
                    logger.error(f"❌ Failed to move to breakeven for {contract_address[:8]}...")
            
            # 3. Проверяем тейк-профиты с новой логикой
            for i, tp_config in enumerate(tp_levels):
                if i in tp_executed:
                    continue  # Этот TP уже выполнен
                
                tp_level = tp_config.get('level', 0) if isinstance(tp_config, dict) else tp_config
                volume_percent = tp_config.get('volume_percent', 25) if isinstance(tp_config, dict) else 25
                
                if tp_level <= 0:
                    logger.warning(f"Invalid TP level at index {i}: {tp_level}")
                    continue
                
                tp_percent = (tp_level - 1) * 100  # Конвертируем множитель в проценты
                
                if pnl_percent >= tp_percent:
                    logger.info(f"🎯 TAKE PROFIT {tp_level}x triggered for {contract_address[:8]}...: PnL {pnl_percent:.2f}% >= {tp_percent:.2f}%")
                    success = self.axiom_client.execute_take_profit(user_id, position, i)
                    if success:
                        # Добавляем индекс выполненного TP
                        new_tp_executed = tp_executed.copy()
                        new_tp_executed.append(i)
                        self.storage.update_position(
                            user_id, 
                            position_id, 
                            {'tp_executed': new_tp_executed}
                        )
                        logger.info(f"✅ Take Profit {tp_level}x executed successfully for {contract_address[:8]}... ({volume_percent}%)")
                    else:
                        logger.error(f"❌ Take Profit {tp_level}x execution failed for {contract_address[:8]}...")
                    
                    # Проверяем, нужно ли удалить позицию после выполнения TP
                    await self.check_position_after_tp(user_id, position, contract_address)
                    
        except Exception as e:
            logger.error(f"Ошибка в check_automation_triggers для {contract_address}: {e}")
    
    async def check_position_after_tp(self, user_id: int, position: Dict, contract_address: str):
        """Проверяем состояние позиции после выполнения TP"""
        try:
            # Получаем актуальный баланс токена
            token_balance = self.axiom_client.get_token_balance(contract_address)
            
            if token_balance <= 0.0001:  # Практически ноль токенов
                logger.info(f"🧹 Position {contract_address[:8]}... has minimal tokens left, removing from tracking")
                self.storage.remove_position(user_id, position['id'])
            else:
                # Обновляем количество токенов в позиции
                self.storage.update_position(
                    user_id, 
                    position['id'], 
                    {'token_amount': token_balance}
                )
                logger.debug(f"Updated token amount for {contract_address[:8]}...: {token_balance}")
                
        except Exception as e:
            logger.error(f"Ошибка при проверке позиции после TP {contract_address}: {e}")
    
    async def start(self):
        """Запускаем мониторинг цен"""
        if self.is_running:
            logger.warning("Price monitor уже запущен")
            return
        
        logger.info(f"📊 Запуск мониторинга цен (интервал: {self.check_interval}с)")
        self.is_running = True
        
        # Создаем HTTP сессию
        self.session = aiohttp.ClientSession()
        
        # Запускаем мониторинг
        self.monitoring_task = asyncio.create_task(self.check_prices())
        
        logger.info("✅ Мониторинг цен запущен")
    
    async def stop(self):
        """Останавливаем мониторинг цен"""
        if not self.is_running:
            return
        
        logger.info("🛑 Остановка мониторинга цен...")
        self.is_running = False
        
        # Отменяем задачу мониторинга
        if self.monitoring_task:
            self.monitoring_task.cancel()
            try:
                await self.monitoring_task
            except asyncio.CancelledError:
                logger.info("📊 Задача мониторинга отменена")
        
        # Закрываем HTTP сессию
        if self.session:
            await self.session.close()
            self.session = None
        
        logger.info("✅ Мониторинг цен остановлен")
    
    async def force_check_position(self, user_id: int, contract_address: str):
        """Принудительная проверка конкретной позиции"""
        try:
            positions = self.storage.get_positions(user_id)
            position = next((p for p in positions if p['contract_address'] == contract_address), None)
            
            if not position:
                logger.warning(f"Position not found for force check: {contract_address}")
                return
            
            current_price = await self.get_token_price(contract_address)
            
            if current_price > 0:
                entry_price = position.get('entry_price', 0)
                if entry_price > 0:
                    pnl_percent = ((current_price - entry_price) / entry_price) * 100
                    
                    # Обновляем позицию
                    self.storage.update_position(
                        user_id,
                        position['id'],
                        {
                            'current_price': current_price, 
                            'pnl': pnl_percent
                        }
                    )
                    
                    # Проверяем автоматические триггеры
                    await self.check_automation_triggers(user_id, position, current_price, pnl_percent)
                    
                    logger.info(f"Force check completed for {contract_address[:8]}...: PnL {pnl_percent:.2f}%")
                else:
                    logger.warning(f"Invalid entry price for position {contract_address}")
            else:
                logger.warning(f"Could not get current price for force check: {contract_address}")
                
        except Exception as e:
            logger.error(f"Ошибка при принудительной проверке позиции {contract_address}: {e}")
    
    def get_monitoring_stats(self) -> Dict:
        """Получаем статистику мониторинга"""
        try:
            positions_data = self.storage.load_positions()
            total_positions = sum(len(positions) for positions in positions_data.values())
            active_users = len(positions_data)
            
            return {
                'is_running': self.is_running,
                'check_interval': self.check_interval,
                'total_positions': total_positions,
                'active_users': active_users,
                'session_active': self.session is not None
            }
        except Exception as e:
            logger.error(f"Ошибка получения статистики мониторинга: {e}")
            return {
                'is_running': False,
                'check_interval': self.check_interval,
                'total_positions': 0,
                'active_users': 0,
                'session_active': False
            }
