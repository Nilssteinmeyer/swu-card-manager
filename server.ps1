# SWU Card Manager — Server-Management (PowerShell)
# Saeubert ALTE Server-Instanzen und startet genau EINEN neuen.

param(
    [switch]$Restart
)

$port = 8765
$projectDir = "C:\Users\LLM\Desktop\TCG-Cardscannprojekt\swu_card_manager"

if ($Restart) {
    Write-Host "== Stoppe alle app.web-Instanzen =="
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like '*app.web*' } |
        ForEach-Object {
            Write-Host "  Beende PID $($_.ProcessId)"
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
    Start-Sleep -Seconds 2

    # Port-Check: wartet bis frei
    for ($i = 0; $i -lt 10; $i++) {
        $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
        if (-not $conns) { break }
        Start-Sleep -Milliseconds 500
    }
}

# Pruefen ob bereits ein Server laeuft
$existing = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    $proc = Get-Process -Id ($existing | Select-Object -First 1).OwningProcess -ErrorAction SilentlyContinue
    Write-Host "== Server laeuft bereits (PID $($proc.Id)) =="
    exit 0
}

Write-Host "== Starte SWU Card Manager =="
Set-Location $projectDir
& "$projectDir\.venv\Scripts\python.exe" -m app.web --host 0.0.0.0 --port $port
