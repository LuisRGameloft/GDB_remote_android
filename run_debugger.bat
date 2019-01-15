@echo off
set CUR_DIR=%cd%

rem Call setting config
call "%CUR_DIR%\config.bat"
call "%PYTHON_PATH%\python.exe" "%CUR_DIR%\script.py"
