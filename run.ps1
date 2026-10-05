Write-Host "Starting Aakhri Tareekh..." -ForegroundColor Green
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { Write-Host "Python not found"; exit 1 }
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
