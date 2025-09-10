from axiomtradeapi import AxiomTradeClient
from typing import Dict, List
from config import AXIOM_ACCESS_TOKEN, AXIOM_REFRESH_TOKEN, WALLET_ADDRESS, PRIVATE_KEY, DEFAULT_SETTINGS
from storage import PositionStorage
import logging
import time
import requests

logger = logging.getLogger(__name__)

class AxiomClient:
    def __init__(self):
        self.api = AxiomTradeClient(
            auth_token=AXIOM_ACCESS_TOKEN,
            refresh_token=AXIOM_REFRESH_TOKEN
        )
        self.wallet_address = WALLET_ADDRESS
        self.private_key = PRIVATE_KEY
        self.storage = PositionStorage()
    
    def get_token_price(self, contract_address: str) -> float:
        """Получаем текущую цену токена через Jupiter API (синхронная версия)"""
        try:
            url = f"https://quote-api.jup.ag/v6/price?ids={contract_address}"
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if 'data' in data and contract_address in data['data']:
                    return float(data['data'][contract_address]['price'])
            logger.warning(f"Failed to get price for {contract_address}")
            return 0.0
        except Exception as e:
            logger.error(f"Ошибка получения цены токена {contract_address}: {e}")
            return 0.0
    
    def get_account_info(self) -> Dict:
        try:
            # Используем правильный метод для получения баланса
            balance_info = self.api.GetBalance(self.wallet_address)
            return {'balance': balance_info.get('sol', 0) if balance_info else 0}
        except Exception as e:
            logger.error(f"Ошибка получения информации о аккаунте: {e}")
            raise
    
    def open_position(self, user_id: int, contract_address: str, amount: float, sl: float, tp: list, breakeven: float, slippage: float = None) -> Dict:
        try:
            # Используем пользовательский слиппедж или значение по умолчанию
            slippage_percent = slippage if slippage is not None else DEFAULT_SETTINGS['slippage_percent']
            
            # Получаем текущую цену токена
            entry_price = self.get_token_price(contract_address)
            
            if entry_price <= 0:
                raise Exception("Не удалось получить цену токена")
            
            logger.info(f"Opening position for {contract_address} at price {entry_price} with {slippage_percent}% slippage")
            
            # Проверяем аутентификацию перед покупкой
            if not self.is_authenticated():
                raise Exception("API не аутентифицирован")
            
            # Используем метод buy_token с пользовательским слиппеджем
            result = self.api.buy_token(
                private_key=self.private_key,
                token_mint=contract_address,
                amount_sol=amount,
                slippage_percent=slippage_percent  # Используем пользовательский слиппедж
            )
            
            if not result.get('success', False):
                raise Exception(f"Ошибка покупки: {result.get('error', 'Unknown error')}")
            
            # Ждем немного для обновления баланса
            time.sleep(3)
            
            # Получаем баланс токена после покупки
            token_balance = self.get_token_balance(contract_address)
            
            # Сохраняем информацию о позиции с новой структурой TP
            position_id = f"{contract_address}_{int(time.time())}"
            position_info = {
                'id': position_id,
                'contract_address': contract_address,
                'invested_sol': amount,
                'token_amount': token_balance,
                'entry_price': entry_price,
                'current_price': entry_price,
                'pnl': 0.0,
                'sl': sl,
                'tp_levels': tp,
                'breakeven_percent': breakeven,
                'slippage_percent': slippage_percent,  # Сохраняем слиппедж в позиции
                'transaction_hash': result.get('signature', ''),
                'timestamp': time.time(),
                'breakeven_moved': False,
                'tp_executed': []
            }
            
            # Сохраняем позицию в хранилище
            self.storage.add_position(user_id, position_info)
            
            logger.info(f"Position opened successfully: {position_id}")
            return position_info
            
        except Exception as e:
            logger.error(f"Ошибка открытия позиции: {e}")
            raise
    
    def close_position(self, user_id: int, contract_address: str, percentage: float = 100.0, slippage: float = None) -> Dict:
        """
        Закрываем позицию полностью или частично
        percentage: процент позиции для закрытия (по умолчанию 100%)
        slippage: пользовательский слиппедж (если None, берем из позиции или настроек по умолчанию)
        """
        try:
            # Получаем слиппедж из позиции или используем переданный
            if slippage is None:
                positions = self.storage.get_positions(user_id)
                position = next((p for p in positions if p['contract_address'] == contract_address), None)
                if position:
                    slippage = position.get('slippage_percent', DEFAULT_SETTINGS['slippage_percent'])
                else:
                    slippage = DEFAULT_SETTINGS['slippage_percent']
            
            logger.info(f"Closing position for {contract_address} ({percentage}%) with {slippage}% slippage")
            
            # Получаем текущий баланс токена
            token_balance = self.get_token_balance(contract_address)
            
            if token_balance <= 0:
                logger.warning(f"No tokens to sell for {contract_address}")
                # Если токенов нет, удаляем позицию из хранилища
                positions = self.storage.get_positions(user_id)
                position_to_remove = next((p for p in positions if p['contract_address'] == contract_address), None)
                if position_to_remove:
                    self.storage.remove_position(user_id, position_to_remove['id'])
                return {'signature': 'no_tokens_to_sell'}
            
            # Рассчитываем количество токенов для продажи
            amount_to_sell = token_balance * (percentage / 100.0)
            
            if not self.is_authenticated():
                raise Exception("API не аутентифицирован")
            
            # Используем метод sell_token с пользовательским слиппеджем
            result = self.api.sell_token(
                private_key=self.private_key,
                token_mint=contract_address,
                amount_tokens=amount_to_sell,
                slippage_percent=slippage  # Используем пользовательский слиппедж
            )
            
            if not result.get('success', False):
                raise Exception(f"Ошибка продажи: {result.get('error', 'Unknown error')}")
            
            # Если продаем все токены (100%), удаляем позицию
            if percentage >= 100.0:
                positions = self.storage.get_positions(user_id)
                position_to_remove = next((p for p in positions if p['contract_address'] == contract_address), None)
                if position_to_remove:
                    self.storage.remove_position(user_id, position_to_remove['id'])
                    logger.info(f"Position removed from storage: {position_to_remove['id']}")
            else:
                # Обновляем количество токенов в позиции
                positions = self.storage.get_positions(user_id)
                position = next((p for p in positions if p['contract_address'] == contract_address), None)
                if position:
                    new_token_amount = self.get_token_balance(contract_address)
                    self.storage.update_position(user_id, position['id'], {'token_amount': new_token_amount})
            
            return result
            
        except Exception as e:
            logger.error(f"Ошибка закрытия позиции: {e}")
            raise
    
    def execute_stop_loss(self, user_id: int, position: Dict) -> bool:
        """Выполняем стоп-лосс"""
        try:
            contract_address = position['contract_address']
            logger.info(f"Executing Stop Loss for {contract_address}")
            
            result = self.close_position(user_id, contract_address, 100.0)
            
            if result.get('success') or result.get('signature'):
                logger.info(f"Stop Loss executed successfully for {contract_address}")
                return True
            else:
                logger.error(f"Stop Loss failed for {contract_address}: {result}")
                return False
                
        except Exception as e:
            logger.error(f"Ошибка выполнения Stop Loss: {e}")
            return False
    
    def execute_take_profit(self, user_id: int, position: Dict, tp_index: int) -> bool:
        """
        Выполняем тейк-профит с новой логикой
        tp_index: индекс TP уровня в массиве tp_levels
        """
        try:
            contract_address = position['contract_address']
            tp_levels = position.get('tp_levels', [])
            
            if tp_index >= len(tp_levels):
                logger.error(f"Invalid TP index {tp_index} for position {contract_address}")
                return False
            
            tp_config = tp_levels[tp_index]
            tp_level = tp_config['level']
            volume_percent = tp_config['volume_percent']
            
            logger.info(f"Executing Take Profit {tp_level}x for {contract_address} ({volume_percent}%)")
            
            result = self.close_position(user_id, contract_address, volume_percent)
            
            if result.get('success') or result.get('signature'):
                logger.info(f"Take Profit {tp_level}x executed successfully for {contract_address}")
                return True
            else:
                logger.error(f"Take Profit {tp_level}x failed for {contract_address}: {result}")
                return False
                
        except Exception as e:
            logger.error(f"Ошибка выполнения Take Profit: {e}")
            return False
    
    def move_to_breakeven(self, user_id: int, position: Dict) -> bool:
        """Перемещаем стоп-лосс в безубыток"""
        try:
            # Обновляем SL до цены входа (0% убыток)
            self.storage.update_position(
                user_id, 
                position['id'], 
                {
                    'sl': 0.0,  # Теперь SL на уровне входа
                    'breakeven_moved': True
                }
            )
            
            contract_address = position['contract_address']
            logger.info(f"Moved stop loss to breakeven for {contract_address}")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка перемещения в безубыток: {e}")
            return False
    
    def get_token_balance(self, contract_address: str) -> float:
        try:
            balance = self.api.get_token_balance(
                wallet_address=self.wallet_address,
                token_mint=contract_address
            )
            logger.debug(f"Token balance for {contract_address}: {balance}")
            return balance if balance is not None else 0.0
        except Exception as e:
            logger.error(f"Ошибка получения баланса токена: {e}")
            return 0.0
    
    def is_authenticated(self) -> bool:
        """Проверяем аутентификацию используя новый метод API"""
        try:
            return self.api.is_authenticated()
        except Exception as e:
            logger.error(f"Ошибка проверки аутентификации: {e}")
            return False
    
    def refresh_tokens(self) -> bool:
        """Обновляем токены доступа"""
        try:
            self.api.refresh_access_token()
            # После обновления получаем новые токены
            tokens = self.api.get_tokens()
            if tokens:
                logger.info("Токены успешно обновлены")
                # Здесь можно сохранить новые токены в конфиг или переменные окружения
                # для следующих запусков
                return True
            return False
        except Exception as e:
            logger.error(f"Ошибка обновления токенов: {e}")
            return False
    
    def get_user_positions(self, user_id: int) -> List[Dict]:
        return self.storage.get_positions(user_id)