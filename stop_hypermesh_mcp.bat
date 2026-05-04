@echo off
taskkill /f /im python.exe /fi "WINDOWTITLE eq *hypermesh_mcp*" 2>nul
echo HyperMesh MCP server stopped.
pause
