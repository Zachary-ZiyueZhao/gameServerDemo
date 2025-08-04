import json
import os

USER_DB_FILE = "users.json"

# 初始化用户文件
def load_users():
    if not os.path.exists(USER_DB_FILE):
        with open(USER_DB_FILE, "w") as f:
            json.dump({"users": {}}, f)
    with open(USER_DB_FILE, "r") as f:
        return json.load(f)

def save_users(users):
    with open(USER_DB_FILE, "w") as f:
        json.dump(users, f, indent=2)

# 登录验证
def verify_login(username, password):
    users = load_users()
    return users["users"].get(username) == password

# 注册新用户
def register_user(username, password):
    users = load_users()
    if username in users["users"]:
        return False
    users["users"][username] = password
    save_users(users)
    return True
