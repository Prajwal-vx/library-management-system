$ErrorActionPreference = "Stop"
if (-not (Test-Path .venv)) { python -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
$config = Get-Content -Raw .env
if ($config.Contains('replace-this-with-a-long-random-secret')) {
  $secret = & .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
  $config = $config.Replace('replace-this-with-a-long-random-secret', $secret.Trim())
  Set-Content -NoNewline -Path .env -Value $config
}
& .\.venv\Scripts\alembic.exe upgrade head
& .\.venv\Scripts\python.exe scripts/seed.py
Write-Host "Ready. Run: .\.venv\Scripts\uvicorn.exe app.main:app --reload"
