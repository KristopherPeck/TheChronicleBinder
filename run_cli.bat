@echo off
setlocal
python -c "from PIL import Image" >nul 2>&1
if errorlevel 1 (
    echo Pillow is required to generate portable EPUB covers.
    echo Install it with: python -m pip install -r "%~dp0requirements.txt"
    exit /b 1
)
set "PYTHONPATH=%~dp0src"
python -m chronicle_binder %*

