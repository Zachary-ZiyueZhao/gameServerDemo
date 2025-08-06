import asyncio, json
from user_store import verify_login, register_user

rooms = {}
room_counter = 1000  # 初始房间ID


async def handle_client(reader, writer):
    addr = writer.get_extra_info('peername')
    print(f"客户端连接：{addr}")

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
                response = {"type": "LOGIN_SUCCESS"}
            else:
                response = {"type": "LOGIN_FAIL"}

        elif message.get("type") == "REGISTER":
            username = message.get("username")
            password = message.get("password")
            if register_user(username, password):
                response = {"type": "REGISTER_SUCCESS"}
            else:
                response = {"type": "REGISTER_FAIL", "msg": "User exists"}

        elif message.get("type") == "CREATE_ROOM":
            global room_counter
            username = message.get("username")
            room_id = str(room_counter)
            room_counter += 1

            # 创建新房间并加入玩家
            rooms[room_id] = {
                "id": room_id,
                "players": [username],
                "status": "waiting"
            }

            response = {
                "type": "CREATE_ROOM_SUCCESS",
                "room_id": room_id
            }

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
            else:
                response = {
                    "type": "JOIN_ROOM_FAIL",
                    "msg": "房间不存在"
                }

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

    print(f"客户端断开：{addr}")
    writer.close()
    await writer.wait_closed()

# 启动服务端
async def main():
    server = await asyncio.start_server(handle_client, '0.0.0.0', 12345)
    print("服务端启动在 12345 端口")
    async with server:
        await server.serve_forever()

asyncio.run(main())
