Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = root
sh.Run """" & root & "\.venv\Scripts\pythonw.exe"" """ & root & "\app.py""", 0, False
