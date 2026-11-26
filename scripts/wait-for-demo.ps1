# Waits (up to ~4 minutes) for the demo page to answer, then saves its address to .demo_url and
# exits 0.
# Exits 1 if it never answers. The port is the DEMO_PORT that setup-demo.ps1 chose (default 8000).
param([int]$Seconds = 240)
Set-Location (Split-Path -Parent $PSScriptRoot)
$port = 8000
if (Test-Path .env) {
    $m = [regex]::Match((Get-Content .env -Raw), "(?m)^DEMO_PORT=(\d+)")
    if ($m.Success) { $port = [int]$m.Groups[1].Value }
}
$health = "http://localhost:$port/health"
$deadline = (Get-Date).AddSeconds($Seconds)
while ((Get-Date) -lt $deadline) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri $health -TimeoutSec 3
        if ($r.StatusCode -eq 200) {
            [IO.File]::WriteAllText((Join-Path (Get-Location) ".demo_url"), "http://localhost:$port/")
            Write-Host ""; Write-Host "http://localhost:$port/"; exit 0
        }
    } catch { }
    Write-Host -NoNewline "."
    Start-Sleep -Seconds 3
}
Write-Host ""
exit 1
