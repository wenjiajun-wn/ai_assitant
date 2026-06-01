import os
import re
import base64
import json
import urllib.request
from io import BytesIO
from datetime import datetime, timedelta, date

from PIL import Image
import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI

CALENDAR_SERVER = "http://127.0.0.1:8080"


def push_to_calendar_server(todos):
    """Push extracted todos directly to the calendar web server."""
    try:
        data = json.dumps(todos, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{CALENDAR_SERVER}/api/events/batch",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("created", 0), None
    except Exception as e:
        return 0, str(e)

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


# ---------- Streamlit App ----------

load_dotenv()

st.set_page_config(page_title="AI 待办事项提取助手", layout="centered")
st.title("📋 AI 视觉 TODO 提取助手")
st.write("上传一张截图，AI 将自动提取待办事项并导入日历网页。")

api_key = os.environ.get("DASHSCOPE_API_KEY")
if not api_key:
    st.error("❌ 未检测到环境变量 DASHSCOPE_API_KEY，请检查 .env 文件。")
    st.stop()

# --- sidebar: Calendar settings ---
with st.sidebar:
    st.header("📅 日历设置")
    st.success("日历网页就绪 ✅")
    st.caption("待办事项将自动导入日历网页 http://127.0.0.1:8080，无需手动操作。")

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
    ("web_synced", False),
    ("web_count", 0),
]:
    if key not in st.session_state:
        st.session_state[key] = default

# --- extraction ---
if image is not None:
    if st.button(" 开始提取并同步到日历", type="primary"):
        # Step 1: AI extraction
        with st.spinner("AI 正在深度分析图片，请稍候..."):
            try:
                prompt = (
                    "用一两句话总结截图中的待办事项，控制在80字以内。"
                    "包含：做什么事、来自哪里、截止时间。同一个日期只出现一次。"
                    "例：需在飞书人事系统申报院外兼职，已从事的须在6月20日前主动报备（来源：人事人才部通知）。"
                    "没有待办内容时回复「✅ 未发现」。"
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
                print(f"\n{'='*40}\nAI 输出:\n{raw}\n{'='*40}\n")
                st.session_state.ai_response = raw
                summary = raw.strip()
                dl = date.today().isoformat()
                patterns = [
                    r'\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?',
                    r'\d{1,2}月\d{1,2}日',
                    r'今天', r'明天', r'后天', r'大后天',
                    r'下周[一二三四五六日天]', r'周[一二三四五六日天]', r'星期[一二三四五六日天]',
                ]
                for pat in patterns:
                    for m in re.findall(pat, summary):
                        d = parse_chinese_date(m)
                        if d > dl:
                            dl = d
                st.session_state.todos = [{
                    "title": summary,
                    "priority": "普通",
                    "date": dl,
                    "source": "截图上传",
                    "deadline": summary,
                }] if summary else []
                st.session_state.web_synced = False
                st.session_state.web_count = 0

            except Exception as e:
                st.error(f"分析过程中出错: {e}")
                st.stop()

        # Step 2: push to calendar website
        todos = st.session_state.todos
        if todos:
            with st.spinner("正在自动同步到日历网页..."):
                web_count, web_err = push_to_calendar_server(todos)
                if web_err:
                    st.warning(f"⚠️ 日历网站同步失败: {web_err}")
                else:
                    st.session_state.web_synced = True
                    st.session_state.web_count = web_count

        st.success("✨ 提取完成！")

# --- show results ---
if st.session_state.ai_response:
    st.subheader("📌 提取到的 TODO 事项：")
    st.markdown(st.session_state.ai_response)

    todos = st.session_state.todos
    if todos:
        st.divider()
        st.subheader("📅 日历导入状态")

        if st.session_state.get("web_synced"):
            st.success(f"🌐 已自动推送 {st.session_state.web_count} 条待办到日历网站")
            st.info("📌 打开 http://127.0.0.1:8080 查看日历")
        else:
            st.warning("⚠️ 同步未成功 — 请确认日历服务器已启动 (双击 run.bat)")
    else:
        st.info("没有可解析的待办事项，未执行日历同步。")
