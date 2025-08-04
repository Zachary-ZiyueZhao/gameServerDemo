import asyncio, json
from user_store import verify_login, register_user

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
