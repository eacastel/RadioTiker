# shared_config.py

import os
import json

CONFIG_FILE = os.path.expanduser("~/.radiotiker_agent_config.json")


def load_user_id():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            data = json.load(f)
            return data.get("user_id")
    return None


def save_user_id(user_id):
    with open(CONFIG_FILE, "w") as f:
        json.dump({"user_id": user_id}, f)


def ensure_user_id():
    user_id = load_user_id()
    if not user_id:
        user_id = input("Enter a unique User ID: ").strip()
        save_user_id(user_id)
    return user_id
