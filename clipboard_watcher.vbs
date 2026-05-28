Set oShell = CreateObject("WScript.Shell")
Set oEnv = oShell.Environment("PROCESS")
oEnv("PYTHONIOENCODING") = "utf-8"
oShell.Run """C:\Users\32030\miniconda3\envs\ai\pythonw.exe"" ""d:\Study\assitant_vision\hotkey_screenshot.py""", 0, False
