import asyncio
import websockets
import json
import os
import signal
import http
from datetime import datetime

# Хранилище комнат
rooms = {}

async def health_check(path, request_headers):
    return (http.HTTPStatus.OK, [], b"OK\n")

async def handler(websocket):
    try:
        async for message in websocket:
            data = json.loads(message)
            action = data.get('action')
            room_id = data.get('room')
            player = data.get('player')
            
            if action == 'join':
                if room_id not in rooms:
                    # Создаем новую комнату
                    rooms[room_id] = {
                        'p1': {'coins': 3, 'enterprises': ['wheat', 'bakery'], 'landmarks': []},
                        'p2': {'coins': 3, 'enterprises': ['wheat', 'bakery'], 'landmarks': []},
                        'turn': 1,
                        'lastRoll': [1, 1],
                        'players': []
                    }
                
                room = rooms[room_id]
                player_num = len(room['players']) + 1
                room['players'].append(websocket)
                
                await websocket.send(json.dumps({
                    'type': 'joined',
                    'player': player_num,
                    'state': room
                }))
                
                # Оповещаем всех
                for ws in room['players']:
                    if ws != websocket:
                        await ws.send(json.dumps({
                            'type': 'gameState',
                            'state': room
                        }))
            
            elif action == 'roll':
                room = rooms.get(room_id)
                if not room:
                    continue
                
                if player != room['turn']:
                    await websocket.send(json.dumps({
                        'type': 'error',
                        'message': 'Сейчас не ваш ход'
                    }))
                    continue
                
                d1, d2 = data.get('dice', [1, 1])
                room['lastRoll'] = [d1, d2]
                
                # Логика доходов
                active = f'p{player}'
                opponent = 'p2' if player == 1 else 'p1'
                
                # Упрощенная логика для примера
                room[active]['coins'] += 1
                
                # Меняем ход
                room['turn'] = 2 if player == 1 else 1
                
                # Рассылаем всем
                for ws in room['players']:
                    await ws.send(json.dumps({
                        'type': 'gameState',
                        'state': room
                    }))
            
            elif action == 'buy':
                room = rooms.get(room_id)
                if not room:
                    continue
                
                if player != room['turn']:
                    continue
                
                card_id = data.get('cardId')
                active = f'p{player}'
                room[active]['coins'] -= 1
                room[active]['enterprises'].append(card_id)
                
                for ws in room['players']:
                    await ws.send(json.dumps({
                        'type': 'gameState',
                        'state': room
                    }))
            
            elif action == 'build':
                room = rooms.get(room_id)
                if not room:
                    continue
                
                if player != room['turn']:
                    continue
                
                landmark_id = data.get('landmarkId')
                active = f'p{player}'
                room[active]['coins'] -= LANDMARK_COSTS[landmark_id]
                room[active]['landmarks'].append(landmark_id)
                
                for ws in room['players']:
                    await ws.send(json.dumps({
                        'type': 'gameState',
                        'state': room
                    }))
                    
    except websockets.exceptions.ConnectionClosed:
        # Удаляем отключившегося игрока
        for room_id, room in list(rooms.items()):
            if websocket in room['players']:
                room['players'].remove(websocket)
                if len(room['players']) == 0:
                    # Удаляем пустую комнату через 5 минут
                    asyncio.create_task(delete_room_delayed(room_id, 300))

async def delete_room_delayed(room_id, delay):
    await asyncio.sleep(delay)
    if room_id in rooms and len(rooms[room_id]['players']) == 0:
        del rooms[room_id]

LANDMARK_COSTS = {
    'station': 4,
    'mall': 10,
    'amusement': 16,
    'tvTower': 22
}

async def main():
    port = int(os.environ.get("PORT", "8080"))
    
    async with websockets.serve(
        handler, 
        "0.0.0.0", 
        port,
        process_request=health_check
    ) as server:
        print(f"✅ Сервер запущен на порту {port}")
        
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, server.close)
        await server.wait_closed()

if __name__ == "__main__":
    asyncio.run(main())
