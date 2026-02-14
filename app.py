import asyncio
import websockets
import json
import os
import http
from datetime import datetime

# Хранилище комнат
rooms = {}

LANDMARK_COSTS = {
    'station': 4, 'mall': 10, 'amusement': 16, 'tvTower': 22
}

async def process_request(path, request_headers):
    """Обработчик HTTP запросов (health check)"""
    # Только для /health, остальное пропускаем (будет WebSocket)
    if path == "/health":
        return (
            http.HTTPStatus.OK,
            [("Content-Type", "text/plain")],
            b"OK\n"
        )
    # Возвращаем None — значит websockets должен обработать как WebSocket
    return None

async def handler(websocket, path):
    """Основной обработчик WebSocket"""
    player_num = None
    room_id = None
    
    print(f"New connection from {websocket.remote_address}")
    
    try:
        async for message in websocket:
            data = json.loads(message)
            action = data.get('action')
            room_id = data.get('room')
            player = data.get('player')
            
            print(f"[{datetime.now()}] {action} | room:{room_id} | player:{player}")
            
            if action == 'join':
                if room_id not in rooms:
                    rooms[room_id] = {
                        'p1': {'coins': 3, 'enterprises': ['wheat', 'bakery'], 'landmarks': []},
                        'p2': {'coins': 3, 'enterprises': ['wheat', 'bakery'], 'landmarks': []},
                        'turn': 1,
                        'lastRoll': [1, 1],
                        'players': []
                    }
                    print(f"Created room: {room_id}")
                
                room = rooms[room_id]
                player_num = len(room['players']) + 1
                
                if player_num > 2:
                    await websocket.send(json.dumps({
                        'type': 'error', 'message': 'Комната заполнена'
                    }))
                    await websocket.close()
                    return
                
                room['players'].append({'ws': websocket, 'num': player_num})
                
                await websocket.send(json.dumps({
                    'type': 'joined',
                    'player': player_num,
                    'state': get_public_state(room)
                }))
                
                await broadcast(room_id, {
                    'type': 'gameState',
                    'state': get_public_state(room)
                })
            
            elif action == 'roll':
                room = rooms.get(room_id)
                if not room or player != room['turn']:
                    continue
                
                d1, d2 = data.get('dice', [1, 1])
                room['lastRoll'] = [d1, d2]
                
                # Простая логика доходов
                active = f'p{player}'
                room[active]['coins'] += 1
                
                room['turn'] = 2 if player == 1 else 1
                
                await broadcast(room_id, {
                    'type': 'gameState',
                    'state': get_public_state(room)
                })
            
            elif action == 'buy':
                room = rooms.get(room_id)
                if not room or player != room['turn']:
                    continue
                
                card_id = data.get('cardId')
                active = f'p{player}'
                
                costs = {'wheat':1, 'ranch':1, 'forest':3, 'bakery':1, 
                        'convenience':2, 'cafe':2, 'familyRest':3, 
                        'stadium':6, 'tvstation':7}
                cost = costs.get(card_id, 1)
                
                if room[active]['coins'] >= cost:
                    room[active]['coins'] -= cost
                    room[active]['enterprises'].append(card_id)
                    
                    await broadcast(room_id, {
                        'type': 'gameState',
                        'state': get_public_state(room)
                    })
            
            elif action == 'build':
                room = rooms.get(room_id)
                if not room or player != room['turn']:
                    continue
                
                lm_id = data.get('landmarkId')
                active = f'p{player}'
                cost = LANDMARK_COSTS.get(lm_id, 0)
                
                if room[active]['coins'] >= cost and lm_id not in room[active]['landmarks']:
                    room[active]['coins'] -= cost
                    room[active]['landmarks'].append(lm_id)
                    
                    if len(room[active]['landmarks']) == 4:
                        await broadcast(room_id, {
                            'type': 'gameOver',
                            'winner': player,
                            'state': get_public_state(room)
                        })
                    else:
                        await broadcast(room_id, {
                            'type': 'gameState',
                            'state': get_public_state(room)
                        })
            
            elif action == 'reset':
                room = rooms.get(room_id)
                if room:
                    room['p1'] = {'coins': 3, 'enterprises': ['wheat', 'bakery'], 'landmarks': []}
                    room['p2'] = {'coins': 3, 'enterprises': ['wheat', 'bakery'], 'landmarks': []}
                    room['turn'] = 1
                    room['lastRoll'] = [1, 1]
                    
                    await broadcast(room_id, {
                        'type': 'gameState',
                        'state': get_public_state(room)
                    })
                    
    except websockets.exceptions.ConnectionClosed:
        print(f"Disconnected player {player_num}")
    finally:
        if room_id and room_id in rooms:
            room = rooms[room_id]
            room['players'] = [p for p in room['players'] if p['ws'] != websocket]
            if len(room['players']) == 0:
                asyncio.create_task(cleanup_room(room_id, 300))

def get_public_state(room):
    """Убираем внутренние объекты перед отправкой"""
    return {
        'p1': room['p1'],
        'p2': room['p2'],
        'turn': room['turn'],
        'lastRoll': room['lastRoll']
    }

async def broadcast(room_id, message):
    """Отправка всем в комнате"""
    room = rooms.get(room_id)
    if not room:
        return
    
    dead = []
    for p in room['players']:
        try:
            await p['ws'].send(json.dumps(message))
        except:
            dead.append(p)
    
    for p in dead:
        room['players'].remove(p)

async def cleanup_room(room_id, delay):
    """Удаление пустой комнаты"""
    await asyncio.sleep(delay)
    if room_id in rooms and len(rooms[room_id]['players']) == 0:
        del rooms[room_id]
        print(f"Deleted room: {room_id}")

async def keep_alive():
    """Предотвращаем засыпание на Render"""
    while True:
        await asyncio.sleep(600)
        print(f"[{datetime.now()}] Keep-alive")

async def main():
    port = int(os.environ.get("PORT", "8000"))
    
    asyncio.create_task(keep_alive())
    
    # ВАЖНО: используем process_request для health check
    async with websockets.serve(
        handler,
        "0.0.0.0",
        port,
        process_request=process_request,  # Только для HTTP
        ping_interval=20,
        ping_timeout=10
    ):
        print(f"✅ Server running on port {port}")
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
