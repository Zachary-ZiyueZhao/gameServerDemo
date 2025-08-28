import asyncio, json
import traceback

from user_store import verify_login, register_user

rooms = {}
room_counter = 1000  # 初始房间ID

connections = {}

def all_players_submitted(room):
    return all(player in room.get("planes", {}) for player in room["players"])

writer_locks = {}  # username -> asyncio.Lock

async def safe_write(username, data):
    """安全地向某个玩家写入数据，带锁+异常保护"""
    if username not in connections:
        return
    lock = writer_locks.setdefault(username, asyncio.Lock())
    async with lock:
        try:
            w = connections[username]
            w.write(data)
            await w.drain()
        except Exception as e:
            print(f"[WARN] 向 {username} 写数据失败: {e}")
            # 连接失效时移除
            try:
                w.close()
                await w.wait_closed()
            except:
                pass
            if username in connections:
                del connections[username]



async def cleanup_empty_rooms():
    while True:
        await asyncio.sleep(5)  # 每 5 秒检查一次
        empty_rooms = []
        for room_id, room in list(rooms.items()):
            if not room.get("players"):
                empty_rooms.append(room_id)

        for room_id in empty_rooms:
            del rooms[room_id]
            print(f"[定时清理] 删除空房间 {room_id}")

async def remove_user_from_rooms(username):
    for room_id in list(rooms.keys()):
        room = rooms[room_id]
        if username in room["players"]:
            index = room["players"].index(username)
            room["players"].pop(index)
            room["writers"].pop(index)
            print(f"已将 {username} 从房间 {room_id} 移除")

            # 如果没人了，清除房间
            if not room["players"]:
                del rooms[room_id]
                print(f"房间 {room_id} 已清除（无人）")
            break

async def handle_client(reader, writer):
    addr = writer.get_extra_info('peername')
    username = None
    print(f"客户端连接：{addr}")
    try:
        while True:
            data = await reader.readline()
            if not data:
                break
            try:
                message = json.loads(data.decode())
            except json.JSONDecodeError:
                continue

            response = {"type": "ERROR", "msg": "Unknown request"}

            if message.get("type") == "LOGIN":
                username = message.get("username")
                password = message.get("password")
                if verify_login(username, password):
                    connections[username] = writer
                    response = {"type": "LOGIN_SUCCESS"}
                else:
                    response = {"type": "LOGIN_FAIL"}

                writer.write((json.dumps(response) + "\n").encode())
                await writer.drain()

            elif message.get("type") == "REGISTER":
                username = message.get("username")
                password = message.get("password")
                if register_user(username, password):
                    response = {"type": "REGISTER_SUCCESS"}
                else:
                    response = {"type": "REGISTER_FAIL", "msg": "User exists"}

                writer.write((json.dumps(response) + "\n").encode())
                await writer.drain()

            elif message.get("type") == "CREATE_ROOM":
                global room_counter
                username = message.get("username")
                room_id = str(room_counter)
                room_counter += 1

                # 创建新房间并加入玩家
                rooms[room_id] = {
                    "id": room_id,
                    "players": [username],
                    "status": "waiting",
                    "planes": {}
                }

                response = {
                    "type": "CREATE_ROOM_SUCCESS",
                    "room_id": room_id
                }

                writer.write((json.dumps(response) + "\n").encode())
                await writer.drain()

            elif message.get("type") == "LIST_ROOMS":
                room_list = []
                for room in rooms.values():
                    room_list.append({
                        "id": room["id"],
                        "players": len(room["players"]),
                        "status": room["status"]
                    })

                response = {
                    "type": "ROOM_LIST",
                    "rooms": room_list
                }

                writer.write((json.dumps(response) + "\n").encode())
                await writer.drain()

            elif message.get("type") == "JOIN_ROOM":
                room_id = message.get("room_id")
                username = message.get("username")

                if room_id in rooms:
                    if username in rooms[room_id]["players"]:
                        response = {
                            "type": "JOIN_ROOM_FAIL",
                            "msg": "你已经在该房间中"
                        }
                    else:
                        rooms[room_id]["players"].append(username)
                        response = {
                            "type": "JOIN_ROOM_SUCCESS",
                            "room_id": room_id,
                            "players": rooms[room_id]["players"]
                        }
                        # 广播房间刷新消息给所有人
                        broadcast = json.dumps({
                            "type": "ROOM_UPDATE",
                            "players": room["players"]
                        }) + "\n"
                else:
                    response = {
                        "type": "JOIN_ROOM_FAIL",
                        "msg": "房间不存在"
                    }

                writer.write((json.dumps(response) + "\n").encode())
                await writer.drain()

            elif message.get("type") == "LEAVE_ROOM":
                username = message.get("username")
                room_id = message.get("room_id")

                if room_id in rooms:
                    room = rooms[room_id]
                    if username in room["players"]:
                        room["players"].remove(username)

                        # 如果房间没人了，删除房间
                        if not room["players"]:
                            del rooms[room_id]

                    response = {
                        "type": "LEAVE_ROOM_SUCCESS"
                    }
                else:
                    response = {
                        "type": "LEAVE_ROOM_FAIL",
                        "msg": "房间不存在"
                    }

                writer.write((json.dumps(response) + "\n").encode())
                await writer.drain()

            elif message.get("type") == "GET_ROOM_INFO":
                room_id = message.get("room_id")
                if room_id in rooms:
                    players = rooms[room_id]["players"]
                    response = {
                        "type": "ROOM_INFO",
                        "players": players
                    }
                else:
                    response = {
                        "type": "ROOM_INFO",
                        "players": []
                    }

                writer.write((json.dumps(response) + "\n").encode())
                await writer.drain()

            elif message.get("type") == "GET_ROOM_STATUS":
                room_id = message.get("room_id")
                if room_id in rooms:
                    status = rooms[room_id].get("status", "waiting")
                    response = {
                        "type": "ROOM_STATUS",
                        "status": status
                    }
                else:
                    response = {
                        "type": "ROOM_STATUS",
                        "status": "not_found"
                    }

                writer.write((json.dumps(response) + "\n").encode())
                await writer.drain()

            elif message.get("type") == "START_GAME":
                room_id = message.get("room_id")
                username = message.get("username")

                if room_id in rooms:
                    room = rooms[room_id]

                    if room["players"][0] == username:
                        room["status"] = "playing"

                        response = {"type": "START_GAME_SUCCESS"}
                    else:
                        response = {"type": "START_GAME_FAIL", "msg": "你不是房主"}
                else:
                    response = {"type": "START_GAME_FAIL", "msg": "房间不存在"}

                writer.write((json.dumps(response) + "\n").encode())
                await writer.drain()

            elif message.get("type") == "SUBMIT_LAYOUT":
                room_id = message["room_id"]
                username = message["username"]
                layout = message["layout"]

                if room_id not in rooms or username not in rooms[room_id]["players"]:
                    await safe_write(username, (json.dumps({"type": "SUBMIT_LAYOUT_FAIL"}) + "\n").encode())
                    return

                # 保存玩家布局
                rooms[room_id].setdefault("planes", {})[username] = layout

                # 单独回复提交成功
                await safe_write(username, (json.dumps({"type": "SUBMIT_LAYOUT_SUCCESS"}) + "\n").encode())

                # 检查是否所有人都提交了
                if all_players_submitted(rooms[room_id]):
                    broadcast_msg = json.dumps({
                        "type": "ALL_LAYOUTS_SUBMITTED",
                        "room_id": room_id,
                        "planes": rooms[room_id]["planes"]
                    }) + "\n"

                    for player in rooms[room_id]["players"]:
                        await safe_write(player, broadcast_msg.encode())
                        await asyncio.sleep(0)  # 让事件循环切换，保证消息不漏

                else:
                    response = {"type": "SUBMIT_LAYOUT_FAIL", "msg": "房间不存在或玩家不在房间"}

                    writer.write((json.dumps(response) + "\n").encode())
                    await writer.drain()

            elif message.get("type") == "ENTER_GAME":
                room_id = message["room_id"]
                username = message["username"]
                if room_id not in rooms:
                    return
                room = rooms[room_id]
                room.setdefault("entered", set()).add(username)

                if len(room["entered"]) == len(room["players"]):
                    # 所有人都进入了
                    first_player = room["players"][0]
                    for p in room["players"]:
                        if p == first_player:
                            await safe_write(p, (json.dumps({"type": "YOUR_TURN"}) + "\n").encode())
                        else:
                            await safe_write(p, (json.dumps({"type": "NOT_YOUR_TURN"}) + "\n").encode())

            elif message.get("type") == "ATTACK":
                room_id = message.get("room_id")
                username = message.get("username")
                x = message.get("x")
                y = message.get("y")

                if room_id not in rooms:
                    return

                room = rooms[room_id]
                if username not in room["players"]:
                    return

                opponents = [p for p in room["players"] if p != username]
                if not opponents:
                    return

                opponent = opponents[0]
                planes = room["planes"].get(opponent, [])

                hit_type = "未击中"
                hit_plane_id = None
                global_dmg = None
                dmg = None

                for plane in planes:
                    if plane["hp"] <= 0:
                        # 🚨 已坠毁飞机仍可能被击中
                        for cell in plane["cells"]:
                            if cell["x"] == x and cell["y"] == y:
                                hit_type = "击中"
                                break
                        if hit_type == "击中":
                            break

                    for cell in plane["cells"]:
                        if cell["x"] == x and cell["y"] == y:
                            dmg = cell["damage"]
                            global_dmg = dmg
                            plane["hp"] -= dmg
                            if plane["hp"] <= 0:
                                hit_type = "坠毁"
                                plane["hp"] = 9999  # ✅ 保持 0 表示坠毁
                            else:
                                hit_type = "击中"
                            hit_plane_id = plane["id"]
                            break
                    if hit_type != "未击中":
                        break

                print(f"[ATTACK] {username} 攻击 ({x},{y}) -> {hit_type}" +
                      (f" 飞机 {hit_plane_id}" if hit_plane_id else ""))

                # 1. 回复攻击方结果
                response = {
                    "type": "ATTACK_RESULT",
                    "result": hit_type,
                    "x": x,
                    "y": y,
                    "dmg": global_dmg
                }
                await safe_write(username, (json.dumps(response) + "\n").encode())

                # 2. 通知被攻击方
                under_attack = {
                    "type": "UNDER_ATTACK",
                    "x": x,
                    "y": y,
                    "result": hit_type,
                    "dmg": dmg
                }
                await safe_write(opponent, (json.dumps(under_attack) + "\n").encode())

                # ✅ 3. 胜负判定
                all_destroyed = all(p["hp"] >= 100 for p in planes)
                if all_destroyed:
                    print(f"[GAME OVER] {username} 获胜, {opponent} 失败")

                    await safe_write(username, (json.dumps({"type": "WIN"}) + "\n").encode())
                    await safe_write(opponent, (json.dumps({"type": "LOSE"}) + "\n").encode())

                    room["status"] = "finished"
                    return  # ✅ 不再切换回合

                # 4. 🔄 切换回合（只有没结束才切换）
                turn_switch = [
                    (username, "NOT_YOUR_TURN"),
                    (opponent, "YOUR_TURN")
                ]

                for u, t in turn_switch:
                    await safe_write(u, (json.dumps({"type": t}) + "\n").encode())
                    await asyncio.sleep(0)


    except Exception as e:
        print(f"[异常] {addr}：{e}")

        print(f"客户端断开：{addr}")
        if username:
            await remove_user_from_rooms(username)
            if username in connections:
                del connections[username]

    writer.close()
    await writer.wait_closed()

# 启动服务端
async def main():
    server = await asyncio.start_server(handle_client, '0.0.0.0', 12345)
    print("服务端启动在 12345 端口")

    # 启动定时清理任务
    asyncio.create_task(cleanup_empty_rooms())

    async with server:
        await server.serve_forever()

asyncio.run(main())
