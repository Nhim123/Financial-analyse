@echo off
REM ============================================================
REM  FinWatch - chay bang Python local (khong can Docker/WSL2)
REM  Mo trinh duyet: http://localhost:8000
REM ============================================================
setlocal
cd /d "%~dp0"

REM Moi truong ao dat NGOAI OneDrive: OneDrive khoa/dong bo file trong .venv gay loi "Access denied".
set "VENV=%USERPROFILE%\.finwatch\venv"

if not exist "%VENV%\Scripts\python.exe" (
    echo [setup] Tao moi truong ao tai %VENV% ...
    python -m venv "%VENV%"
)

echo [setup] Cai/ cap nhat thu vien ...
"%VENV%\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt

set "DB_PATH=%~dp0data\finwatch.db"
set "COLLECT_INTERVAL_SECONDS=600"

echo.
echo [run] Mo trinh duyet tai http://localhost:8000
echo [run] Nhan Ctrl+C de dung.
echo.
REM --timeout-graceful-shutdown: luong gia truc tiep (/stream) giu ket noi mo, khong cho vo han khi tat.
"%VENV%\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 5

endlocal
