@echo off
setlocal
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 (
    echo Python 3.11 or newer is required.
    exit /b 1
)
python -c "import PIL; raise SystemExit(0 if int(PIL.__version__.split('.')[0]) == 13 else 1)" >nul 2>&1
if errorlevel 1 (
    echo A Pillow 13.x release is required to generate portable EPUB covers securely.
    echo Install it with: python -m pip install -r "%~dp0requirements.txt"
    exit /b 1
)
set "PYTHONPATH=%~dp0src"
python -m chronicle_binder %*

