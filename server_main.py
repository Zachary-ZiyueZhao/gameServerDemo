import asyncio, json
import traceback

from user_store import verify_login, register_user

rooms = {}
room_counter = 1000  # 初始房间ID

connections = {}

def all_players_submitted(room):
    return all(player in room.get("planes", {}) for player in room["players"])


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
                room_id = message.get("room_id")
                username = message.get("username")
                layout = message.get("layout")
                if room_id in rooms and username in rooms[room_id]["players"]:

                    if "planes" not in rooms[room_id]:
                        rooms[room_id]["planes"] = {}
                    rooms[room_id]["planes"][username] = layout

                    # 判断是否所有玩家都提交了
                    if all_players_submitted(rooms[room_id]):
                        # 广播给该房间所有玩家，告诉他们布局都提交了，客户端可以进入下一界面
                        broadcast_msg = json.dumps({
                            "type": "ALL_LAYOUTS_SUBMITTED",
                            "room_id": room_id,
                            "planes": rooms[room_id]["planes"]
                        }) + "\n"

                        # rooms[room_id]["players"] 是玩家列表
                        for player in rooms[room_id]["players"]:
                            writer_for_player = connections.get(player)
                            if writer_for_player:
                                writer_for_player.write(broadcast_msg.encode())
                                await writer_for_player.drain()

                    response = {"type": "SUBMIT_LAYOUT_SUCCESS"}

                else:
                    response = {"type": "SUBMIT_LAYOUT_FAIL", "msg": "房间不存在或玩家不在房间"}

                writer.write((json.dumps(response) + "\n").encode())
                await writer.drain()



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
    async with server:
        await server.serve_forever()

asyncio.run(main())
