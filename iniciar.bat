@echo off
title Bot BHD
cd /d "%~dp0"
echo.
echo   Bot BHD en http://localhost:5004/probar?clave=bot123
echo   (necesita la variable ANTHROPIC_API_KEY seteada)
echo.
python app.py
pause
