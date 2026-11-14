@echo off
setlocal
set "PYTHONPATH=%~dp0src"
python -m librarian_assistant --gui
if errorlevel 1 pause

