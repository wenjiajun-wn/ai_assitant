"""
剪贴板监听助手 — 后台运行，自动监测截图。
Win+Shift+S → AI 分析 → 自动打开日历网页 → 事项已就位。
无需登录，无需注册，即开即用。
"""

import os
import sys
import re
import hashlib
import base64
import time
import json
import webbrowser
import urllib.request
from io import BytesIO
from datetime import datetime, timedelta, date

from PIL import Image
from dotenv import load_dotenv
from openai import OpenAI
import keyboard


# ── 多用户配置：从 user_config.json 读取用户名和服务器地址 ──
def get_base_dir():
    """获取 exe 或脚本所在目录（兼容 PyInstaller 打包）"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

BASE_DIR = get_base_dir()

def load_user_config():
    """读取配置，无 Token 时自动引导登录/注册。"""
    config_path = os.path.join(BASE_DIR, "user_config.json")
    defaults = {
        "user_id": "default",
        "server_url": "http://47.84.108.154:8080",
        "api_token": ""
    }
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            defaults.update(cfg)
        except (json.JSONDecodeError, ValueError):
            pass

    if not defaults.get("api_token"):
        server = defaults["server_url"]
        print("\n  Welcome to AI TODO Assistant!")
        print("  Opening browser for one-time setup...")
        print("  Register or login on the webpage to complete setup.")
        webbrowser.open(server + "/login")

        # Wait for the webpage to save config (poll up to 2 minutes)
        for _ in range(240):
            time.sleep(0.5)
            if os.path.exists(config_path):
                try:
                    with open(config_path, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                    if cfg.get("api_token"):
                        defaults.update(cfg)
                        print("  Setup complete! Starting...\n")
                        break
                except Exception:
                    pass
        else:
            print("  Timed out waiting for setup. Please run again.")
            sys.exit(1)

    return defaults


def _call_api(server, path, data):
    """Helper: POST JSON to server, return parsed response."""
    req = urllib.request.Request(
        server + path,
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _try_login(server, email, password):
    try:
        r = _call_api(server, "/api/auth/login", {"email": email, "password": password})
        return r.get("api_token") if r.get("ok") else None
    except Exception:
        return None


def _try_register(server, email, password):
    try:
        r = _call_api(server, "/api/auth/register", {"email": email, "password": password})
        if r.get("ok") and r.get("api_token"):
            return r["api_token"]
        return None
    except Exception:
        return None

USER_CONFIG = load_user_config()
USER_ID = USER_CONFIG["user_id"]
API_TOKEN = USER_CONFIG["api_token"]
CALENDAR_SERVER = USER_CONFIG["server_url"]
PENDING_FILE = os.path.join(BASE_DIR, "pending_todos.json")
_browser_opened = False


def push_to_calendar_server(todos):
    """Push extracted todos directly to the calendar web server.
    On failure, save to local pending file so calendar picks them up later."""
    # Try push
    try:
        data = json.dumps(todos, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{CALENDAR_SERVER}/api/events/batch",
            data=data,
            headers={"Content-Type": "application/json", "X-API-Token": API_TOKEN},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("created", 0), None
    except Exception as e:
        pass  # Will save to pending file below

    # Server not reachable — save to pending file for later import
    try:
        existing = []
        if os.path.exists(PENDING_FILE):
            with open(PENDING_FILE, "r", encoding="utf-8") as f:
                existing = json.loads(f.read())
        existing.extend(todos)
        with open(PENDING_FILE, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        return len(todos), f"服务器未启动，已暂存到本地，打开日历网页后自动导入"
    except Exception as e2:
        return 0, str(e2)


def open_calendar():
    """Open calendar in browser on first push only."""
    global _browser_opened
    if not _browser_opened:
        webbrowser.open(f"{CALENDAR_SERVER}/login")
        _browser_opened = True

# ---------- helpers ----------

def pil_to_base64(image):
    max_size = 2048
    w, h = image.size
    if max(w, h) > max_size:
        ratio = max_size / max(w, h)
        image = image.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
    if image.mode in ("RGBA", "P"):
        image = image.convert("RGB")
    buf = BytesIO()
    image.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


WEEKDAY_CN = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}


def parse_chinese_date(text):
    today = date.today()
    if not text:
        return today.isoformat()
    text = text.strip()

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass

    if text == "今天":
        return today.isoformat()
    if text == "明天":
        return (today + timedelta(days=1)).isoformat()
    if text == "后天":
        return (today + timedelta(days=2)).isoformat()
    if text == "大后天":
        return (today + timedelta(days=3)).isoformat()

    m = re.match(r"下周([一二三四五六日天])", text)
    if m:
        d = WEEKDAY_CN[m.group(1)]
        days = (d - today.weekday()) % 7 + 7
        return (today + timedelta(days=days)).isoformat()

    m = re.match(r"(?:周|星期)([一二三四五六日天])", text)
    if m:
        d = WEEKDAY_CN[m.group(1)]
        days = (d - today.weekday()) % 7
        if days == 0:
            days = 7
        return (today + timedelta(days=days)).isoformat()

    m = re.match(r"(\d{1,2})月(\d{1,2})日?", text)
    if m:
        try:
            return date(today.year, int(m.group(1)), int(m.group(2))).isoformat()
        except ValueError:
            pass

    return today.isoformat()


def parse_todos_from_markdown(md):
    todos = []
    priority = "普通"
    for line in md.split("\n"):
        line = line.strip()
        if "紧急" in line:
            priority = "紧急"
        elif "重要" in line:
            priority = "重要"
        elif "普通" in line:
            priority = "普通"
        m = re.match(
            r"-\s*\*\*任务\*\*[::]\s*(.+?)\s*\|\s*\*\*来源\*\*[：:]\s*(.+?)(?:\s*\|\s*\*\*截止\*\*[：:]\s*(.+))?$",
            line,
        )
        if m:
            deadline_raw = m.group(3).strip() if m.group(3) else None
            todos.append({
                "title": m.group(1).strip(),
                "source": m.group(2).strip(),
                "deadline": deadline_raw or "未指定",
                "priority": priority,
                "date": parse_chinese_date(deadline_raw),
            })
    return todos


def notify(title, message):
    try:
        from win10toast import ToastNotifier
        ToastNotifier().show_toast(title, message, duration=5, threaded=True)
    except Exception:
        print(f"[{title}] {message}")


def image_hash(img):
    return hashlib.md5(img.tobytes()).hexdigest()


# ---------- main ----------

def single_instance_lock():
    """Prevent multiple instances of this script from running (file-based lock)."""
    import tempfile, subprocess
    lockfile = os.path.join(tempfile.gettempdir(), "ai_todo_watcher.lock")
    if os.path.exists(lockfile):
        try:
            with open(lockfile) as f:
                old_pid = int(f.read().strip())
            # Windows: use tasklist to check if PID exists
            r = subprocess.run(f'tasklist /fi \"pid eq {old_pid}\" /fo csv /nh',
                             shell=True, capture_output=True)
            if f'"{old_pid}"' in r.stdout.decode('gbk', errors='ignore'):
                print("⚠️ 截图监听已在运行中，请勿重复启动")
                sys.exit(0)
            # Stale lock
            os.remove(lockfile)
        except (ValueError, OSError):
            os.remove(lockfile)
    with open(lockfile, "w") as f:
        f.write(str(os.getpid()))
    return lockfile


def main():
    lock = single_instance_lock()
    load_dotenv(os.path.join(BASE_DIR, ".env"))

    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        notify("AI TODO 助手", "未找到 DASHSCOPE_API_KEY，请检查 .env")
        sys.exit(1)

    client = OpenAI(
        api_key=api_key,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        timeout=120.0,
    )

    # Check calendar server (should already be started by run.bat)
    print("📅 检查日历服务器...")
    try:
        urllib.request.urlopen(
            urllib.request.Request(f"{CALENDAR_SERVER}/api/events",
                                   headers={"X-API-Token": API_TOKEN}), timeout=2)
        print(f"   日历网页就绪 → {CALENDAR_SERVER}")
    except Exception:
        print(f"   ⚠️ 日历服务器未启动，事项将暂存本地，打开后自动导入")

    print("🔔 截图监听已启动（轮询剪贴板模式，无需管理员权限）")
    print("   Win+Shift+S 截图 → 自动捕获全屏 → AI 分析 → 日历")
    print("   Ctrl+C 退出")

    last_hashes = []
    processing = False
    cooldown_until = 0

    def process_screenshot():
        nonlocal processing, cooldown_until
        if processing or time.time() < cooldown_until:
            return
        processing = True
        try:
            time.sleep(0.5)
            import win32clipboard
            win32clipboard.OpenClipboard()
            if not win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_DIB):
                win32clipboard.CloseClipboard()
                print("   ⚠️ 剪贴板无图片，请确认已用 Win+Shift+S 截图")
                return
            data = win32clipboard.GetClipboardData(win32clipboard.CF_DIB)
            win32clipboard.CloseClipboard()
            img = Image.open(BytesIO(data))
            h = image_hash(img)
            key = (h, img.size[0], img.size[1])
            if key in last_hashes:
                return
            last_hashes.append(key)
            if len(last_hashes) > 10:
                last_hashes.pop(0)

            print(f"\n📸 屏幕捕获 ({img.size}) — AI 分析中...")
            img_b64 = pil_to_base64(img)
            response = client.chat.completions.create(
                model="qwen-vl-max",
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                        {"type": "text", "text": "仔细查看这张截图，用一段简洁的话总结其中需要关注或处理的事项。控制在100字以内。用中文回答。"},
                    ],
                }],
            )
            raw = response.choices[0].message.content
            print(f"   AI: {raw}")
            summary = raw.strip()
            if not summary:
                print("   ✅ 无事项")
                return
            dl = date.today().isoformat()
            for pat in [r'\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?', r'\d{1,2}月\d{1,2}日',
                         '今天', '明天', '后天', '大后天',
                         r'下周[一二三四五六日天]', r'周[一二三四五六日天]', r'星期[一二三四五六日天]']:
                for m in re.findall(pat, summary):
                    d = parse_chinese_date(m)
                    if d > dl:
                        dl = d
            todos = [{"title": summary, "priority": "普通", "date": dl, "source": "截图", "deadline": summary}]
            _, err = push_to_calendar_server(todos)
            if err:
                print(f"   ⚠️ 同步失败: {err}")
            else:
                print("   🌐 已推送到日历")
                open_calendar()
            notify("AI TODO 助手", summary[:60])
        except Exception as e:
            print(f"   ❌ 失败: {e}")
        finally:
            processing = False
            cooldown_until = time.time() + 5

    keyboard.add_hotkey('win+shift+s', process_screenshot, suppress=False)
    try:
        keyboard.wait()
    except KeyboardInterrupt:
        print("\n👋 已退出")
    finally:
        if os.path.exists(lock):
            os.remove(lock)


if __name__ == "__main__":
    main()
