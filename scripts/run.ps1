$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# Load APP_HOST, APP_PORT, and DASHBOARD_PUBLIC_URL from .env if present
$AppHost = "0.0.0.0"
$AppPort = "8020"
$PublicUrl = ""

$EnvFile = Join-Path $Root ".env"
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $parts = $line.Split("=", 2)
            $key = $parts[0].Trim()
            $val = $parts[1].Trim()
            if ($key -eq "APP_HOST" -and $val) { $AppHost = $val }
            elseif ($key -eq "APP_PORT" -and $val) { $AppPort = $val }
            elseif ($key -eq "DASHBOARD_PUBLIC_URL" -and $val) { $PublicUrl = $val }
        }
    }
}

$DisplayUrl = if ($PublicUrl) { $PublicUrl } else { "http://${AppHost}:${AppPort}" }
Write-Host "Starting CUCM AI Health Monitor on ${AppHost}:${AppPort} (Dashboard: ${DisplayUrl}) ..."
& ".\.venv\Scripts\python.exe" -m uvicorn backend.main:app --host $AppHost --port $AppPort
