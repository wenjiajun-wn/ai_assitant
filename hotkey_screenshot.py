"""
剪贴板监听助手 — 后台运行，自动监测截图。
Win+Shift+S / 微信截图 / QQ截图 → AI 分析 → 自动导入 Windows 日历。
无需登录，无需注册，即开即用。
"""

import os
import sys
import re
import hashlib
import base64
import time
from io import BytesIO
from datetime import datetime, timedelta, date

from PIL import Image, ImageGrab
from dotenv import load_dotenv
from openai import OpenAI
from calendar_sync import add_to_calendar

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


# ---------- Calendar sync ----------
# Uses local Windows Calendar via .ics auto-import — no cloud setup needed.


# ---------- main ----------

def main():
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        notify("AI TODO 助手", "未找到 DASHSCOPE_API_KEY，请检查 .env")
        sys.exit(1)

    client = OpenAI(
        api_key=api_key,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        timeout=120.0,
    )

    print("🔔 AI TODO 剪贴板监听已启动")
    print("   截图 (Win+Shift+S) → AI 分析 → 导入 Windows 日历")
    print("   关闭此窗口即可退出")

    last_hash = None
    processing = False

    while True:
        try:
            img = ImageGrab.grabclipboard()
            if img is None or not isinstance(img, Image.Image):
                time.sleep(1)
                continue

            h = image_hash(img)
            if h == last_hash:
                time.sleep(1)
                continue
            last_hash = h

            if processing:
                continue
            processing = True

            try:
                print(f"\n📸 检测到新截图 ({img.size}) — 正在 AI 分析...")

                prompt = (
                    "仔细查看这张截图，逐区域扫描其中的文字内容。把你看到的每一个任务、待办、提醒、"
                    "@提及、未读消息、会议安排、截止日期、代码 TODO/FIXME 都列出来。\n\n"
                    '重要：即使只有一条模糊的任务线索也要列出来，不要轻易判断"没有待办事项"。\n\n'
                    "按以下格式输出：\n"
                    "### 🔴 紧急\n"
                    "- **任务**:xxx | **来源**:xxx | **截止**:xxx\n"
                    "### 🟡 重要\n"
                    "- **任务**:xxx | **来源**:xxx | **截止**:xxx\n"
                    "### 🟢 普通\n"
                    "- **任务**:xxx | **来源**:xxx\n\n"
                    "只有在你逐区域扫描后、确实找不到任何文字提及任务相关内容时，才回复：'✅ 未在屏幕中发现待办事项。'\n"
                    "用中文回答。"
                )

                img_b64 = pil_to_base64(img)
                response = client.chat.completions.create(
                    model="qwen-vl-max",
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                            {"type": "text", "text": prompt},
                        ],
                    }],
                )

                raw = response.choices[0].message.content
                print(f"   AI: {raw}")

                todos = parse_todos_from_markdown(raw)

                if not todos:
                    print("   ✅ 未发现待办事项")
                    notify("AI TODO 助手", "未发现待办事项")
                    continue

                # Print results
                print(f"   共提取 {len(todos)} 条待办:")
                for t in todos:
                    print(f"      [{t['priority']}] {t['title']} → {t['date']}")

                # Add to Windows Calendar via .ics auto-import
                count, err = add_to_calendar(todos)
                if err:
                    print(f"   ⚠️ 日历导入失败: {err}")
                else:
                    print(f"   📅 已自动导入 {count} 条待办到 Outlook 日历")

                # Notification
                summary = "、".join([t["title"][:15] for t in todos[:3]])
                if len(todos) > 3:
                    summary += f" 等{len(todos)}条"
                notify("AI TODO 助手 — 已导入", f"{summary}\n已自动导入到 Outlook 日历")

            except Exception as e:
                print(f"   ❌ 处理失败: {e}")
                notify("AI TODO 助手 — 错误", str(e))
                last_hash = None
            finally:
                processing = False

        except KeyboardInterrupt:
            print("\n👋 已退出")
            break
        except Exception:
            time.sleep(1)


if __name__ == "__main__":
    main()
