import os
import re
import base64
from io import BytesIO
from datetime import datetime, timedelta, date

from PIL import Image
import streamlit as st
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
            r"-\s*\*\*任务\*\*[：:]\s*(.+?)\s*\|\s*\*\*来源\*\*[：:]\s*(.+?)(?:\s*\|\s*\*\*截止\*\*[：:]\s*(.+))?$",
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


def sync_to_icloud(todos):
    """Add todos to Windows Calendar via .ics auto-import. Returns (created, error)."""
    count, err = add_to_calendar(todos)
    if err:
        return [], err
    return [{"title": t["title"]} for t in todos[:count]], None


def generate_ics(todos):
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//AI TODO Helper//",
    ]
    for item in todos:
        dt = item["date"].replace("-", "")
        uid = f"{dt}-{abs(hash(item['title'])) & 0x7FFFFFFF:08x}"
        lines += [
            "BEGIN:VEVENT",
            f"DTSTART;VALUE=DATE:{dt}",
            f"DTEND;VALUE=DATE:{dt}",
            f"SUMMARY:[{item['priority']}] {item['title']}",
            f"DESCRIPTION:来源: {item['source']}\\\\n截止: {item['deadline']}",
            f"UID:{uid}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)


# ---------- Streamlit App ----------

load_dotenv()

st.set_page_config(page_title="AI 待办事项提取助手", layout="centered")
st.title("📋 AI 视觉 TODO 提取助手")
st.write("上传一张截图，AI 将为你自动提取待办事项并导入 Windows 日历。")

api_key = os.environ.get("DASHSCOPE_API_KEY")
if not api_key:
    st.error("❌ 未检测到环境变量 DASHSCOPE_API_KEY，请检查 .env 文件。")
    st.stop()

# --- sidebar: Calendar settings ---
with st.sidebar:
    st.header("📅 日历设置")
    st.success("日历就绪 ✅")
    st.caption("待办事项将自动导入 Outlook 日历，无需手动操作。")

client = OpenAI(
    api_key=api_key,
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    timeout=120.0,
)

uploaded_file = st.file_uploader("选择或拖拽图片文件到此处...", type=["png", "jpg", "jpeg"])
st.info("💡 提示：Win `Win + Shift + S` / Mac `Cmd + Shift + 4` 截图后拖入。")

image = None
if uploaded_file is not None:
    image = Image.open(uploaded_file)
    st.image(image, caption="已加载的截图", use_container_width=True)

# --- session state init ---
for key, default in [
    ("todos", []),
    ("ai_response", ""),
    ("outlook_synced", False),
    ("outlook_count", 0),
]:
    if key not in st.session_state:
        st.session_state[key] = default

# --- extraction ---
if image is not None:
    if st.button("🚀 开始提取并同步到日历", type="primary"):
        # Step 1: AI extraction
        with st.spinner("AI 正在深度分析图片，请稍候..."):
            try:
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

                img_b64 = pil_to_base64(image)
                response = client.chat.completions.create(
                    model="qwen-vl-max",
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                            {"type": "text", "text": prompt},
                        ],
                    }],
                )

                raw = response.choices[0].message.content
                st.session_state.ai_response = raw
                st.session_state.todos = parse_todos_from_markdown(raw)
                st.session_state.outlook_synced = False
                st.session_state.outlook_count = 0

            except Exception as e:
                st.error(f"分析过程中出错: {e}")
                st.stop()

        # Step 2: sync to iCloud
        todos = st.session_state.todos
        if todos:
            with st.spinner("正在自动同步到 Outlook 日历..."):
                created, err = sync_to_icloud(todos)
                if err:
                    st.warning(f"⚠️ 日历同步失败: {err}")
                else:
                    st.session_state.outlook_synced = True
                    st.session_state.outlook_count = len(created)

        st.success("✨ 提取完成！")

# --- show results ---
if st.session_state.ai_response:
    st.subheader("📌 提取到的 TODO 事项：")
    st.markdown(st.session_state.ai_response)

    todos = st.session_state.todos
    if todos:
        st.divider()
        st.subheader("📅 日历导入状态")

        if st.session_state.outlook_synced:
            st.success(f"✅ 已自动导入 {st.session_state.outlook_count} 条待办到 Outlook 日历")
        else:
            st.warning("⚠️ 导入未成功，可使用下方 .ics 文件手动导入")

        ics_content = generate_ics(todos)
        st.download_button(
            label="📥 下载 .ics 日历文件（备用）",
            data=ics_content,
            file_name=f"todos_{datetime.now().strftime('%Y%m%d_%H%M%S')}.ics",
            mime="text/calendar",
        )
        st.caption("如果自动导入失败，可下载 .ics 文件后双击手动导入。")
    else:
        st.info("没有可解析的待办事项，未执行日历同步。")
