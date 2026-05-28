"""
Calendar sync module — imports events directly into Outlook/Windows Calendar.
Primary: Outlook COM automation (silent, no popups).
Fallback: .ics file auto-open (one-click save).
"""

import os
import sys
import subprocess
import tempfile
from datetime import datetime, timedelta, date


def _add_via_outlook_com(todos):
    """Create calendar events silently via Outlook COM automation.
    Returns (created_count, error_string_or_None).
    """
    import win32com.client

    outlook = win32com.client.Dispatch("Outlook.Application")
    namespace = outlook.GetNamespace("MAPI")
    calendar = namespace.GetDefaultFolder(9)  # olFolderCalendar

    count = 0
    for item in todos:
        try:
            dt = date.fromisoformat(item["date"])
            appt = outlook.CreateItem(1)  # olAppointmentItem
            appt.Subject = f"[{item['priority']}] {item['title']}"
            appt.Body = f"来源: {item['source']}\n截止: {item['deadline']}"
            appt.Start = f"{dt.isoformat()} 00:00"
            appt.End = f"{dt.isoformat()} 23:59"
            appt.AllDayEvent = True
            appt.ReminderSet = True
            appt.ReminderMinutesBeforeStart = 15
            appt.Save()
            count += 1
        except Exception as e:
            print(f"  创建事件失败 [{item['title']}]: {e}")

    return count, None


def _add_via_ics_file(todos):
    """Fallback: generate .ics file and open with default calendar app.
    User needs to click Save once. Returns (created_count, error_string_or_None).
    """
    try:
        ics_content = generate_ics(todos)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        ics_path = os.path.join(tempfile.gettempdir(), f"AI-TODO-{ts}.ics")
        with open(ics_path, "w", encoding="utf-8") as f:
            f.write(ics_content)

        if sys.platform == "win32":
            os.startfile(ics_path)
        elif sys.platform == "darwin":
            subprocess.run(["open", ics_path])
        else:
            subprocess.run(["xdg-open", ics_path])

        return len(todos), None

    except Exception as e:
        return 0, str(e)


def generate_ics(todos):
    """Generate .ics calendar file content from todo items."""
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
            f"DESCRIPTION:来源: {item['source']}\\n截止: {item['deadline']}",
            f"UID:{uid}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)


def add_to_calendar(todos):
    """Add todos to calendar — silent Outlook COM if available, otherwise .ics fallback.

    Returns (created_count, error_string_or_None).
    """
    if not todos:
        return 0, None

    # Primary: Outlook COM automation (silent, no user interaction)
    try:
        import win32com.client
        outlook = win32com.client.Dispatch("Outlook.Application")
        outlook.Name  # force early check — raises if Outlook unavailable
        return _add_via_outlook_com(todos)
    except Exception:
        pass

    # Fallback: .ics file auto-open (one-click save)
    return _add_via_ics_file(todos)


def sync_to_icloud(todos):
    """Alias for add_to_calendar — kept for compatibility."""
    return add_to_calendar(todos)


def is_logged_in():
    """No login required — always ready."""
    return True
