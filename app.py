import asyncio
import websockets
import json
import os
import signal
import http
from datetime import datetime

# Хранилище комнат
rooms = {}

# Константы стоимости достопримечательностей
LANDMARK_COSTS = {
    'station': 4,
    'mall': 10,
    'amusement': 16,
    'tvTower': 22
}

async def health_check(path, request_headers):
    """Обработчик для health check - всегда возвращает OK"""
    print(f"Health check received at path: {path}")
    return (http.HTTPStatus.OK, [], b"OK\n")

async def handler(websocket):
    """Основной обработчик WebSocket-соединений"""
    try:
        async for message in websocket:
            data = json.loads(message)
            action = data.get('action')
            room_id = data.get('room')
            player = data.get('player')
            
            print(f"Action: {action}, Room: {room_id}, Player: {player}")
            
            if action == 'join':
                # Подключение к комнате
                if room_id not in rooms:
                    # Создаем новую комнату
                    rooms[room_id] = {
                        'p1': {'coins': 3, 'enterprises': ['wheat', 'bakery'], 'landmarks': []},
                        'p2': {'coins': 3, 'enterprises': ['wheat', 'bakery'], 'landmarks': []},
                        'turn': 1,
                        'lastRoll': [1, 1],
                        'players': []
                    }
                    print(f"Created new room: {room_id}")
                
                room = rooms[room_id]
                player_num = len(room['players']) + 1
                
                if player_num > 2:
                    await websocket.send(json.dumps({
                        'type': 'error',
                        'message': 'Комната заполнена'
                    }))
                    return
                
                room['players'].append(websocket)
                
                await websocket.send(json.dumps({
                    'type': 'joined',
                    'player': player_num,
                    'state': room
                }))
                
                # Оповещаем всех в комнате
                await broadcast_to_room(room_id, {
                    'type': 'gameState',
                    'state': room
                })
            
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
                
                # Упрощенная логика для теста
                room[active]['coins'] += 1
                
                # Меняем ход
                room['turn'] = 2 if player == 1 else 1
                
                # Рассылаем всем
                await broadcast_to_room(room_id, {
                    'type': 'gameState',
                    'state': room
                })
            
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
                
                await broadcast_to_room(room_id, {
                    'type': 'gameState',
                    'state': room
                })
            
            elif action == 'build':
                room = rooms.get(room_id)
                if not room:
                    continue
                
                if player != room['turn']:
                    continue
                
                landmark_id = data.get('landmarkId')
                active = f'p{player}'
                cost = LANDMARK_COSTS.get(landmark_id, 0)
                room[active]['coins'] -= cost
                room[active]['landmarks'].append(landmark_id)
                
                await broadcast_to_room(room_id, {
                    'type': 'gameState',
                    'state': room
                })
            
            elif action == 'reset':
                room = rooms.get(room_id)
                if room:
                    room['p1'] = {'coins': 3, 'enterprises': ['wheat', 'bakery'], 'landmarks': []}
                    room['p2'] = {'coins': 3, 'enterprises': ['wheat', 'bakery'], 'landmarks': []}
                    room['turn'] = 1
                    room['lastRoll'] = [1, 1]
                    
                    await broadcast_to_room(room_id, {
                        'type': 'gameState',
                        'state': room
                    })
                    
    except websockets.exceptions.ConnectionClosed:
        print("Client disconnected")
        # Удаляем отключившегося игрока
        for room_id, room in list(rooms.items()):
            if websocket in room['players']:
                room['players'].remove(websocket)
                print(f"Player removed from room {room_id}, {len(room['players'])} players left")
                if len(room['players']) == 0:
                    # Удаляем пустую комнату через 5 минут
                    asyncio.create_task(delete_room_delayed(room_id, 300))

async def broadcast_to_room(room_id, message):
    """Отправка сообщения всем в комнате"""
    room = rooms.get(room_id)
    if not room:
        return
    
    dead_sockets = []
    for ws in room['players']:
        try:
            await ws.send(json.dumps(message))
        except Exception as e:
            print(f"Error sending to client: {e}")
            dead_sockets.append(ws)
    
    # Удаляем отвалившиеся соединения
    for ws in dead_sockets:
        if ws in room['players']:
            room['players'].remove(ws)

async def delete_room_delayed(room_id, delay):
    """Удаление комнаты через delay секунд"""
    await asyncio.sleep(delay)
    if room_id in rooms and len(rooms[room_id]['players']) == 0:
        del rooms[room_id]
        print(f"Room {room_id} deleted after timeout")

async def main():
    port = int(os.environ.get("PORT", "8000"))
    
    print(f"Starting server on port {port}")
    
    async with websockets.serve(
        handler, 
        "0.0.0.0", 
        port,
        process_request=health_check  # Важно! Обрабатываем health check
    ) as server:
        print(f"✅ Сервер запущен на порту {port}")
        print(f"🌐 WebSocket URL: wss://{os.environ.get('KOYEB_PUBLIC_HOST', 'localhost')}")
        
        # Держим сервер запущенным
        await asyncio.Future()  # Бесконечное ожидание

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nServer stopped by user")
    except Exception as e:
        print(f"Fatal error: {e}")
