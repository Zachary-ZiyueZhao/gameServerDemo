import asyncio
import json
from dataclasses import dataclass, field
from secrets import randbelow

from user_store import register_user, verify_login

BOARD_SIZE = 10
MAX_PLAYERS = 2
PLANE_COUNT = 3
PLANE_HP = 10
BASE_DAMAGE = (
    (0, 0, 10, 0, 0),
    (2, 2, 5, 2, 2),
    (0, 0, 5, 0, 0),
    (0, 3, 3, 3, 0),
)


@dataclass
class Room:
    room_id: str
    players: list[str]
    status: str = "waiting"  # waiting -> placing -> playing -> finished
    planes: dict[str, list[dict]] = field(default_factory=dict)
    entered_players: set[str] = field(default_factory=set)
    attacked_cells: dict[str, set[tuple[int, int]]] = field(default_factory=dict)
    current_turn: str | None = None


rooms: dict[str, Room] = {}
connections: dict[str, asyncio.StreamWriter] = {}
writer_locks: dict[str, asyncio.Lock] = {}


def generate_room_id() -> str:
    """Return a six-digit room number that is not currently in use."""
    while True:
        room_id = f"{randbelow(900_000) + 100_000}"
        if room_id not in rooms:
            return room_id


async def send_to(username: str, message: dict) -> None:
    writer = connections.get(username)
    if writer is None:
        return

    lock = writer_locks.setdefault(username, asyncio.Lock())
    async with lock:
        try:
            writer.write((json.dumps(message, ensure_ascii=False) + "\n").encode())
            await writer.drain()
        except (ConnectionError, OSError):
            await disconnect_user(username)


async def broadcast(room: Room, message: dict) -> None:
    await asyncio.gather(*(send_to(player, message) for player in room.players))


async def publish_room_update(room: Room) -> None:
    await broadcast(room, {
        "type": "ROOM_UPDATE",
        "room_id": room.room_id,
        "players": room.players,
        "status": room.status,
    })


def room_list() -> list[dict]:
    return [
        {"id": room.room_id, "players": len(room.players), "status": room.status}
        for room in rooms.values()
    ]


def rotate_damage(damage_map: tuple[tuple[int, ...], ...]) -> tuple[tuple[int, ...], ...]:
    rows, columns = len(damage_map), len(damage_map[0])
    return tuple(tuple(damage_map[rows - 1 - column][row] for column in range(rows)) for row in range(columns))


def build_plane(plane_id: int, x: int, y: int, rotation: int) -> dict | None:
    damage_map = BASE_DAMAGE
    for _ in range(rotation):
        damage_map = rotate_damage(damage_map)

    cells = []
    for row, damage_row in enumerate(damage_map):
        for column, damage in enumerate(damage_row):
            if damage == 0:
                continue
            cell_x, cell_y = x + column, y + row
            if not (0 <= cell_x < BOARD_SIZE and 0 <= cell_y < BOARD_SIZE):
                return None
            cells.append({"x": cell_x, "y": cell_y, "damage": damage})
    return {"id": plane_id, "head_x": x, "head_y": y, "rotation": rotation, "hp": PLANE_HP, "cells": cells}


def normalize_layout(layout: object) -> list[dict] | None:
    """Build the authoritative aircraft data from positions only."""
    if not isinstance(layout, list) or len(layout) != PLANE_COUNT:
        return None
    occupied: set[tuple[int, int]] = set()
    normalized = []
    for plane_id, plane in enumerate(layout, start=1):
        if not isinstance(plane, dict):
            return None
        x, y, rotation = plane.get("head_x"), plane.get("head_y"), plane.get("rotation")
        if not all(isinstance(value, int) for value in (x, y, rotation)) or rotation not in range(4):
            return None
        plane_data = build_plane(plane_id, x, y, rotation)
        if plane_data is None:
            return None
        for cell in plane_data["cells"]:
            position = (cell["x"], cell["y"])
            if position in occupied:
                return None
            occupied.add(position)
        normalized.append(plane_data)
    return normalized


def find_room_for_player(username: str) -> Room | None:
    return next((room for room in rooms.values() if username in room.players), None)


async def remove_from_room(username: str) -> None:
    room = find_room_for_player(username)
    if room is None:
        return

    room.players.remove(username)
    room.planes.pop(username, None)
    room.entered_players.discard(username)
    room.attacked_cells.pop(username, None)
    if not room.players:
        rooms.pop(room.room_id, None)
        return

    if room.status in {"placing", "playing"}:
        await broadcast(room, {"type": "OPPONENT_LEFT", "room_id": room.room_id})
        rooms.pop(room.room_id, None)
        return
    await publish_room_update(room)


async def disconnect_user(username: str) -> None:
    connections.pop(username, None)
    writer_locks.pop(username, None)
    await remove_from_room(username)


async def handle_attack(username: str, message: dict) -> None:
    room = find_room_for_player(username)
    if room is None or room.room_id != message.get("room_id"):
        await send_to(username, {"type": "ERROR", "msg": "你不在此房间中"})
        return
    if room.status != "playing" or room.current_turn != username:
        await send_to(username, {"type": "ERROR", "msg": "现在不能攻击"})
        return
    x, y = message.get("x"), message.get("y")
    if not isinstance(x, int) or not isinstance(y, int) or not (0 <= x < BOARD_SIZE and 0 <= y < BOARD_SIZE):
        await send_to(username, {"type": "ERROR", "msg": "攻击坐标无效"})
        return
    attacks = room.attacked_cells.setdefault(username, set())
    if (x, y) in attacks:
        await send_to(username, {"type": "ERROR", "msg": "该位置已经攻击过"})
        return
    attacks.add((x, y))

    opponent = next(player for player in room.players if player != username)
    hit_type, damage = "未击中", 0
    target_planes = room.planes[opponent]
    for plane in target_planes:
        if plane["hp"] <= 0:
            continue
        hit_cell = next((cell for cell in plane["cells"] if cell["x"] == x and cell["y"] == y), None)
        if hit_cell is None:
            continue
        damage = hit_cell["damage"]
        plane["hp"] = max(0, plane["hp"] - damage)
        hit_type = "坠毁" if plane["hp"] == 0 else "击中"
        break

    await send_to(username, {"type": "ATTACK_RESULT", "result": hit_type, "x": x, "y": y, "dmg": damage})
    await send_to(opponent, {"type": "UNDER_ATTACK", "x": x, "y": y, "result": hit_type, "dmg": damage})
    if all(plane["hp"] == 0 for plane in target_planes):
        room.status = "finished"
        room.current_turn = None
        await send_to(username, {"type": "WIN"})
        await send_to(opponent, {"type": "LOSE"})
        rooms.pop(room.room_id, None)
        return

    room.current_turn = opponent
    await send_to(username, {"type": "NOT_YOUR_TURN"})
    await send_to(opponent, {"type": "YOUR_TURN"})


async def handle_message(username: str | None, message: dict) -> None:
    message_type = message.get("type")
    if username is None:
        return

    if message_type == "LIST_ROOMS":
        await send_to(username, {"type": "ROOM_LIST", "rooms": room_list()})
    elif message_type == "CREATE_ROOM":
        if find_room_for_player(username) is not None:
            await send_to(username, {"type": "CREATE_ROOM_FAIL", "msg": "请先离开当前房间"})
        else:
            room_id = generate_room_id()
            room = Room(room_id=room_id, players=[username])
            rooms[room_id] = room
            await send_to(username, {"type": "CREATE_ROOM_SUCCESS", "room_id": room_id, "players": room.players})
    elif message_type == "JOIN_ROOM":
        room = rooms.get(message.get("room_id"))
        if room is None or room.status != "waiting":
            await send_to(username, {"type": "JOIN_ROOM_FAIL", "msg": "房间不存在或不能加入"})
        elif find_room_for_player(username) is not None:
            await send_to(username, {"type": "JOIN_ROOM_FAIL", "msg": "请先离开当前房间"})
        elif len(room.players) >= MAX_PLAYERS:
            await send_to(username, {"type": "JOIN_ROOM_FAIL", "msg": "房间已满"})
        else:
            room.players.append(username)
            await send_to(username, {"type": "JOIN_ROOM_SUCCESS", "room_id": room.room_id, "players": room.players})
            await publish_room_update(room)
    elif message_type == "LEAVE_ROOM":
        await remove_from_room(username)
        await send_to(username, {"type": "LEAVE_ROOM_SUCCESS"})
    elif message_type == "START_GAME":
        room = find_room_for_player(username)
        if room is None or room.room_id != message.get("room_id"):
            await send_to(username, {"type": "START_GAME_FAIL", "msg": "房间不存在"})
        elif room.players[0] != username or len(room.players) != MAX_PLAYERS:
            await send_to(username, {"type": "START_GAME_FAIL", "msg": "需要房主和两名玩家才能开始"})
        else:
            room.status = "placing"
            room.planes.clear()
            room.entered_players.clear()
            room.attacked_cells.clear()
            await broadcast(room, {"type": "GAME_STARTED", "room_id": room.room_id})
            await publish_room_update(room)
    elif message_type == "SUBMIT_LAYOUT":
        room = find_room_for_player(username)
        layout = normalize_layout(message.get("layout"))
        if room is None or room.room_id != message.get("room_id") or room.status != "placing" or layout is None:
            await send_to(username, {"type": "SUBMIT_LAYOUT_FAIL", "msg": "布局无效或游戏状态不正确"})
        else:
            room.planes[username] = layout
            await send_to(username, {"type": "SUBMIT_LAYOUT_SUCCESS"})
            if len(room.planes) == len(room.players):
                await broadcast(room, {"type": "ALL_LAYOUTS_SUBMITTED", "room_id": room.room_id, "planes": room.planes})
    elif message_type == "ENTER_GAME":
        room = find_room_for_player(username)
        if room is not None and room.room_id == message.get("room_id") and len(room.planes) == len(room.players):
            room.entered_players.add(username)
            if len(room.entered_players) == len(room.players):
                room.status, room.current_turn = "playing", room.players[0]
                await send_to(room.current_turn, {"type": "YOUR_TURN"})
                await send_to(room.players[1], {"type": "NOT_YOUR_TURN"})
    elif message_type == "ATTACK":
        await handle_attack(username, message)
    else:
        await send_to(username, {"type": "ERROR", "msg": "未知请求"})


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    username: str | None = None
    peer = writer.get_extra_info("peername")
    try:
        while line := await reader.readline():
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict):
                continue
            message_type = message.get("type")
            if message_type == "REGISTER":
                requested_name, password = message.get("username"), message.get("password")
                success = isinstance(requested_name, str) and isinstance(password, str) and register_user(requested_name, password)
                writer.write((json.dumps({"type": "REGISTER_SUCCESS" if success else "REGISTER_FAIL"}, ensure_ascii=False) + "\n").encode())
                await writer.drain()
                continue
            if message_type == "LOGIN":
                requested_name, password = message.get("username"), message.get("password")
                if username is not None or not isinstance(requested_name, str) or not isinstance(password, str) or not verify_login(requested_name, password):
                    writer.write((json.dumps({"type": "LOGIN_FAIL"}, ensure_ascii=False) + "\n").encode())
                    await writer.drain()
                    continue
                old_writer = connections.get(requested_name)
                username = requested_name
                connections[username] = writer
                if old_writer is not None and old_writer is not writer:
                    old_writer.close()
                await send_to(username, {"type": "LOGIN_SUCCESS"})
                continue
            await handle_message(username, message)
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        if username is not None and connections.get(username) is writer:
            await disconnect_user(username)
        writer.close()
        await writer.wait_closed()
        print(f"客户端断开：{peer}")


async def main() -> None:
    server = await asyncio.start_server(handle_client, "0.0.0.0", 12345)
    print("服务端启动在 12345 端口")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
