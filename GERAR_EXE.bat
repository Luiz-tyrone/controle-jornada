@echo off
cd /d "%~dp0"
py -m pip install -r requirements.txt
if errorlevel 1 (echo Falha ao instalar dependencias & pause & exit /b 1)
py -m PyInstaller --noconfirm --clean --onefile --windowed --add-data "logo_agtlog.png;." --add-data "agt_log.ico;." --icon "agt_log.ico" --name "Controle de Jornada AGT LOG" controle_jornada.py
if errorlevel 1 (echo Falha ao gerar EXE & pause & exit /b 1)
echo EXE criado na pasta dist
pause
