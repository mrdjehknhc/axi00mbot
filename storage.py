import json
import os
from typing import Dict, List, Optional

class PositionStorage:
    def __init__(self, filename: str = 'positions.json'):
        self.filename = filename
        self.ensure_file_exists()
    
    def ensure_file_exists(self):
        if not os.path.exists(self.filename):
            with open(self.filename, 'w') as f:
                json.dump({}, f)
    
    def load_positions(self) -> Dict:
        with open(self.filename, 'r') as f:
            return json.load(f)
    
    def save_positions(self, positions: Dict):
        with open(self.filename, 'w') as f:
            json.dump(positions, f, indent=2)
    
    def add_position(self, user_id: int, position_data: Dict):
        positions = self.load_positions()
        if str(user_id) not in positions:
            positions[str(user_id)] = []
        positions[str(user_id)].append(position_data)
        self.save_positions(positions)
    
    def remove_position(self, user_id: int, position_id: str):
        positions = self.load_positions()
        if str(user_id) in positions:
            user_positions = positions[str(user_id)]
            positions[str(user_id)] = [p for p in user_positions if p['id'] != position_id]
            self.save_positions(positions)
    
    def get_positions(self, user_id: int) -> List[Dict]:
        positions = self.load_positions()
        return positions.get(str(user_id), [])
    
    def update_position(self, user_id: int, position_id: str, updates: Dict):
        positions = self.load_positions()
        if str(user_id) in positions:
            for i, position in enumerate(positions[str(user_id)]):
                if position['id'] == position_id:
                    positions[str(user_id)][i].update(updates)
                    break
            self.save_positions(positions)