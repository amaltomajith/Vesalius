# First-run setup for the demo. Idempotent: it only CREATES what is missing and never overwrites
# anything that exists (changing a database password after the database volume exists would lock the
# app out of its own database).
#
#   .env                                  passwords, MinIO keys, de-identification secrets
#   secrets\postgres_superuser_password   must equal POSTGRES_SUPERUSER_PASSWORD in .env
#   secrets\minio_root_password           MinIO admin password
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$utf8 = New-Object System.Text.UTF8Encoding($false)   # no BOM: a BOM would become part of a password

function New-Secret([int]$Bytes = 24) {
    $b = New-Object byte[] $Bytes
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($b)
    return ([BitConverter]::ToString($b) -replace '-', '').ToLower()
}

function Get-EnvValue([string]$Text, [string]$Name) {
    $m = [regex]::Match($Text, "(?m)^$Name=(.*)$")
    if ($m.Success) { return $m.Groups[1].Value.Trim() }
    return ""
}

New-Item -ItemType Directory -Force -Path secrets | Out-Null

if (-not (Test-Path .env)) {
    if (-not (Test-Path .env.example)) { throw ".env.example is missing - the download looks incomplete." }
    $text = Get-Content .env.example -Raw
    $text = [regex]::Replace($text, "changeme-[a-z\-]+", { param($m) New-Secret 12 })
    $text = [regex]::Replace($text, "(?m)^DEID_UID_SECRET=.*$", "DEID_UID_SECRET=$(New-Secret 32)")
    $text = [regex]::Replace($text, "(?m)^DEID_DATE_SHIFT_SECRET=.*$", "DEID_DATE_SHIFT_SECRET=$(New-Secret 32)")
    [IO.File]::WriteAllText((Join-Path (Get-Location) ".env"), $text, $utf8)
    Write-Host "  created .env with fresh random passwords"
}
else {
    # an existing .env is kept, except that empty de-identification secrets (S4 refuses to run
    # without them) are filled in
    $text = Get-Content .env -Raw
    $changed = $false
    foreach ($name in "DEID_UID_SECRET", "DEID_DATE_SHIFT_SECRET") {
        if ((Get-EnvValue $text $name) -eq "") {
            if ($text -match "(?m)^$name=") { $text = [regex]::Replace($text, "(?m)^$name=.*$", "$name=$(New-Secret 32)") }
            else { $text = $text.TrimEnd() + "`r`n$name=$(New-Secret 32)`r`n" }
            $changed = $true
        }
    }
    if ($changed) {
        [IO.File]::WriteAllText((Join-Path (Get-Location) ".env"), $text, $utf8)
        Write-Host "  filled in the missing de-identification secrets in .env"
    }
}

# The page's port. 8000 is very commonly taken by other software, so the first free port from a
# short list is chosen ONCE and remembered in .env (later runs keep it: the demo may be using it).
function Test-PortFree([int]$Port) {
    try {
        $l = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $Port)
        $l.Start(); $l.Stop(); return $true
    } catch { return $false }
}
$envText = Get-Content .env -Raw
if ((Get-EnvValue $envText "DEMO_PORT") -eq "") {
    $chosen = $null
    foreach ($p in 8000, 8001, 8080, 8088, 8123, 8765, 18000) {
        if (Test-PortFree $p) { $chosen = $p; break }
    }
    if ($null -eq $chosen) { throw "No free port found for the demo page (tried 8000, 8001, 8080, 8088, 8123, 8765, 18000)." }
    [IO.File]::WriteAllText((Join-Path (Get-Location) ".env"), ($envText.TrimEnd() + "`r`nDEMO_PORT=$chosen`r`n"), $utf8)
    Write-Host "  the demo page will use port $chosen"
}
$envText = Get-Content .env -Raw
$pgSecret = Join-Path (Get-Location) "secrets\postgres_superuser_password"
if (-not (Test-Path $pgSecret)) {
    $pw = Get-EnvValue $envText "POSTGRES_SUPERUSER_PASSWORD"
    if ($pw -eq "") { throw "POSTGRES_SUPERUSER_PASSWORD is empty in .env" }
    [IO.File]::WriteAllText($pgSecret, $pw, $utf8)
    Write-Host "  created secrets\postgres_superuser_password"
}
$minioSecret = Join-Path (Get-Location) "secrets\minio_root_password"
if (-not (Test-Path $minioSecret)) {
    [IO.File]::WriteAllText($minioSecret, (New-Secret 16), $utf8)
    Write-Host "  created secrets\minio_root_password"
}
Write-Host "  setup complete"
