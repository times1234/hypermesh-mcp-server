' HyperMesh MCP SSE Server - silent background launcher
' Double-click to start the server with no visible window

Dim WshShell, strCommand
Set WshShell = CreateObject("Wscript.Shell")

' Set environment variables
Dim env
Set env = WshShell.Environment("Process")
env("HYPERMESH_BATCH_EXE") = "F:\Program Files\Altair\2020\hwdesktop\hw\bin\win64\hmbatch.exe"

' Run Python server hidden (0 = hidden window, False = don't wait)
strCommand = "python ""F:\mcp\hypermesh_mcp_server.py"" --transport sse --host 127.0.0.1 --port 8742"
WshShell.Run strCommand, 0, False

Set WshShell = Nothing
