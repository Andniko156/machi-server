import asyncio
import websockets
import json
import os
import http
from datetime import datetime

rooms = {}  # {room_id: {...}}
clients = {}  # {websocket: {room_id, player_num, last_ping}}

LANDMARK_COSTS = {
    'station': 4, 'mall': 10, 'amusement': 16, 'tvTower': 22
}

async def process_request(path, request_headers):
    if path == "/health":
        return (http.HTTPStatus.OK, [("Content-Type", "text/plain")], b"OK\n")
    if path == "/rooms":
        # API для получения списка комнат
        rooms_data = []
        for rid, room in rooms.items():
            rooms_data.append({
                'id': rid,
                'players': len(room['players']),
                'maxPlayers': 2,
                'turn': room['turn']
            })
        return (http.HTTPStatus.OK, 
                [("Content-Type", "application/json"), 
                 ("Access-Control-Allow-Origin", "*")],
                json.dumps(rooms_data).encode())
    return None

async def handler(websocket, path):
    client_info = {'room_id': None, 'player_num': None}
    clients[websocket] = client_info
    
    print(f"[CONNECT] {websocket.remote_address}")
    
    try:
        async for message in websocket:
            data = json.loads(message)
            action = data.get('action')
            
            # Пинг для поддержания соединения
            if action == 'ping':
                await websocket.send(json.dumps({'type': 'pong', 'time': datetime.now().isoformat()}))
                continue
            
            room_id = data.get('room')
            player = data.get('player')
            
            if action == 'join':
                await handle_join(websocket, room_id, client_info)
            
            elif action == 'leave':
                await handle_leave(websocket, client_info)
            
            elif action == 'getRooms':
                await send_rooms_list(websocket)
            
            elif action == 'roll':
                await handle_roll(room_id, player, data.get('dice', [1,1]))
            
            elif action == 'buy':
                await handle_buy(room_id, player, data.get('cardId'))
            
            elif action == 'build':
                await handle_build(room_id, player, data.get('landmarkId'))
            
            elif action == 'reset':
                await handle_reset(room_id)
            
            elif action == 'startGame':
                await handle_start(room_id)
                    
    except websockets.exceptions.ConnectionClosed:
        print(f"[DISCONNECT] Connection closed")
    except Exception as e:
        print(f"[ERROR] {e}")
    finally:
        await cleanup_client(websocket, client_info)

async def handle_join(websocket, room_id, client_info):
    if not room_id:
        await websocket.send(json.dumps({'type': 'error', 'message': 'Укажите ID комнаты'}))
        return
    
    # Удаляем из старой комнаты если был
    if client_info['room_id']:
        await remove_from_room(websocket, client_info['room_id'])
    
    # Создаем или получаем комнату
    if room_id not in rooms:
        rooms[room_id] = {
            'p1': {'coins': 3, 'enterprises': ['wheat', 'bakery'], 'landmarks': [], 'name': 'Игрок 1'},
            'p2': {'coins': 3, 'enterprises': ['wheat', 'bakery'], 'landmarks': [], 'name': 'Игрок 2'},
            'turn': 0,  # 0 = ожидание, 1 или 2 = игра
            'lastRoll': [1, 1],
            'players': [],
            'created': datetime.now().isoformat()
        }
        print(f"[ROOM] Created: {room_id}")
    
    room = rooms[room_id]
    
    # Проверяем не заполнена ли
    if len(room['players']) >= 2:
        await websocket.send(json.dumps({'type': 'error', 'message': 'Комната заполнена'}))
        return
    
    # Назначаем слот
    player_num = 1 if not room['players'] else 2
    client_info['room_id'] = room_id
    client_info['player_num'] = player_num
    
    room['players'].append({
        'ws': websocket,
        'num': player_num,
        'ready': False
    })
    
    # Обновляем имя если передано
    name = data.get('name', f'Игрок {player_num}')
    room[f'p{player_num}']['name'] = name[:20]  # ограничение 20 символов
    
    await websocket.send(json.dumps({
        'type': 'joined',
        'player': player_num,
        'room': room_id,
        'state': get_public_state(room)
    }))
    
    # Уведомляем других
    await broadcast(room_id, {
        'type': 'playerJoined',
        'player': player_num,
        'name': name,
        'state': get_public_state(room)
    }, exclude=websocket)
    
    # Если 2 игрока — предлагаем начать
    if len(room['players']) == 2:
        await broadcast(room_id, {
            'type': 'canStart',
            'message': 'Нажмите НАЧАТЬ ИГРУ когда будете готовы'
        })

async def handle_leave(websocket, client_info):
    if client_info['room_id']:
        await remove_from_room(websocket, client_info['room_id'])
        client_info['room_id'] = None
        client_info['player_num'] = None
        await websocket.send(json.dumps({'type': 'left', 'message': 'Вы покинули комнату'}))

async def remove_from_room(websocket, room_id):
    if room_id not in rooms:
        return
    
    room = rooms[room_id]
    room['players'] = [p for p in room['players'] if p['ws'] != websocket]
    
    print(f"[ROOM] {room_id}: {len(room['players'])} players left")
    
    # Если никого не осталось — удаляем сразу
    if len(room['players']) == 0:
        if room_id in rooms:
            del rooms[room_id]
            print(f"[ROOM] Deleted empty room: {room_id}")
    else:
        # Уведомляем оставшегося
        remaining = room['players'][0]
        room['turn'] = 0  # Сбрасываем игру
        await remaining['ws'].send(json.dumps({
            'type': 'playerLeft',
            'message': 'Соперник вышел. Ожидание нового игрока...',
            'state': get_public_state(room)
        }))

async def cleanup_client(websocket, client_info):
    if client_info['room_id']:
        await remove_from_room(websocket, client_info['room_id'])
    if websocket in clients:
        del clients[websocket]

async def send_rooms_list(websocket):
    rooms_data = []
    for rid, room in rooms.items():
        rooms_data.append({
            'id': rid,
            'players': len(room['players']),
            'maxPlayers': 2,
            'status': 'Играет' if room['turn'] > 0 else 'Ожидание',
            'playerNames': [room['p1']['name'], room['p2']['name']] if room['p2']['name'] != 'Игрок 2' else [room['p1']['name']]
        })
    
    await websocket.send(json.dumps({
        'type': 'roomsList',
        'rooms': rooms_data
    }))

async def handle_start(room_id):
    if room_id not in rooms:
        return
    
    room = rooms[room_id]
    if len(room['players']) != 2:
        return
    
    room['turn'] = 1
    room['p1']['coins'] = 3
    room['p2']['coins'] = 3
    room['p1']['enterprises'] = ['wheat', 'bakery']
    room['p2']['enterprises'] = ['wheat', 'bakery']
    room['p1']['landmarks'] = []
    room['p2']['landmarks'] = []
    
    await broadcast(room_id, {
        'type': 'gameStarted',
        'state': get_public_state(room),
        'message': 'Игра началась! Ход Игрока 1'
    })

async def handle_roll(room_id, player, dice):
    room = rooms.get(room_id)
    if not room or player != room['turn']:
        return
    
    d1, d2 = dice
    room['lastRoll'] = [d1, d2]
    dice_sum = d1 + d2
    
    active_key = f'p{player}'
    opponent_key = 'p2' if player == 1 else 'p1'
    
    # Логика доходов (упрощенная)
    process_income(room, active_key, opponent_key, dice_sum, d1 == d2)
    
    room['turn'] = 2 if player == 1 else 1
    
    await broadcast(room_id, {
        'type': 'gameState',
        'state': get_public_state(room),
        'lastRoll': [d1, d2],
        'nextPlayer': room['turn']
    })

def process_income(room, active, opponent, dice_sum, is_double):
    p_active = room[active]
    p_opp = room[opponent]
    
    # Зеленые (все)
    if dice_sum == 1 and 'wheat' in p_active['enterprises']:
        p_active['coins'] += 1
    if dice_sum == 1 and 'wheat' in p_opp['enterprises']:
        p_opp['coins'] += 1
    
    if dice_sum == 2 and 'ranch' in p_active['enterprises']:
        p_active['coins'] += 1
    if dice_sum == 2 and 'ranch' in p_opp['enterprises']:
        p_opp['coins'] += 1
    
    if dice_sum == 5 and 'forest' in p_active['enterprises']:
        p_active['coins'] += 1
    if dice_sum == 5 and 'forest' in p_opp['enterprises']:
        p_opp['coins'] += 1
    
    # Синие (свой ход)
    if dice_sum in [2, 3] and 'bakery' in p_active['enterprises']:
        p_active['coins'] += 1
    if dice_sum == 4 and 'convenience' in p_active['enterprises']:
        p_active['coins'] += 2
    
    # Красные (кража)
    if dice_sum == 3 and 'cafe' in p_opp['enterprises']:
        steal = min(1, p_active['coins'])
        p_active['coins'] -= steal
        p_opp['coins'] += steal
    
    if dice_sum in [3, 4] and 'familyRest' in p_opp['enterprises']:
        steal = min(2, p_active['coins'])
        p_active['coins'] -= steal
        p_opp['coins'] += steal
    
    # Фиолетовые
    if dice_sum == 6:
        if 'stadium' in p_active['enterprises']:
            steal = min(2, p_opp['coins'])
            p_opp['coins'] -= steal
            p_active['coins'] += steal
        if 'tvstation' in p_active['enterprises']:
            steal = min(3, p_opp['coins'])
            p_opp['coins'] -= steal
            p_active['coins'] += steal
    
    # Парк развлечений
    if is_double and 'amusement' in p_active['landmarks']:
        p_active['coins'] += 5

async def handle_buy(room_id, player, card_id):
    room = rooms.get(room_id)
    if not room or player != room['turn']:
        return
    
    costs = {
        'wheat': 1, 'ranch': 1, 'forest': 3,
        'bakery': 1, 'convenience': 2,
        'cafe': 2, 'familyRest': 3,
        'stadium': 6, 'tvstation': 7
    }
    
    cost = costs.get(card_id, 1)
    p = room[f'p{player}']
    
    if p['coins'] >= cost:
        p['coins'] -= cost
        p['enterprises'].append(card_id)
        
        await broadcast(room_id, {
            'type': 'gameState',
            'state': get_public_state(room),
            'message': f"Куплено: {card_id}"
        })

async def handle_build(room_id, player, landmark_id):
    room = rooms.get(room_id)
    if not room or player != room['turn']:
        return
    
    cost = LANDMARK_COSTS.get(landmark_id, 0)
    p = room[f'p{player}']
    
    if p['coins'] >= cost and landmark_id not in p['landmarks']:
        p['coins'] -= cost
        p['landmarks'].append(landmark_id)
        
        # Проверка победы
        if len(p['landmarks']) == 4:
            await broadcast(room_id, {
                'type': 'gameOver',
                'winner': player,
                'winnerName': p['name'],
                'state': get_public_state(room)
            })
        else:
            await broadcast(room_id, {
                'type': 'gameState',
                'state': get_public_state(room),
                'message': f"Построено: {landmark_id}"
            })

async def handle_reset(room_id):
    if room_id not in rooms:
        return
    
    room = rooms[room_id]
    room['p1'] = {'coins': 3, 'enterprises': ['wheat', 'bakery'], 'landmarks': [], 'name': room['p1']['name']}
    room['p2'] = {'coins': 3, 'enterprises': ['wheat', 'bakery'], 'landmarks': [], 'name': room['p2'].get('name', 'Игрок 2')}
    room['turn'] = 1
    room['lastRoll'] = [1, 1]
    
    await broadcast(room_id, {
        'type': 'gameState',
        'state': get_public_state(room),
        'message': 'Игра сброшена'
    })

def get_public_state(room):
    return {
        'p1': room['p1'],
        'p2': room['p2'],
        'turn': room['turn'],
        'lastRoll': room['lastRoll'],
        'playerCount': len(room['players'])
    }

async def broadcast(room_id, message, exclude=None):
    if room_id not in rooms:
        return
    
    dead = []
    for p in rooms[room_id]['players']:
        if p['ws'] == exclude:
            continue
        try:
            await p['ws'].send(json.dumps(message))
        except:
            dead.append(p)
    
    for p in dead:
        rooms[room_id]['players'].remove(p)

async def keep_alive():
    while True:
        await asyncio.sleep(600)
        print(f"[{datetime.now()}] Keep-alive")

async def main():
    port = int(os.environ.get("PORT", "8000"))
    asyncio.create_task(keep_alive())
    
    async with websockets.serve(
        handler, "0.0.0.0", port,
        process_request=process_request,
        ping_interval=20,
        ping_timeout=10
    ):
        print(f"✅ Server on port {port}")
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
