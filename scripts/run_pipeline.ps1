# scripts\run_pipeline.ps1
# Run from F:\Projects\DE Pipelining in PowerShell:
#   .\scripts\run_pipeline.ps1 -ZipPath sample_data\ct.zip -Source demo

param(
    [Parameter(Mandatory=$true)][string]$ZipPath,
    [string]$Source = "demo"
)

$python = "F:\Projects\DE Pipelining\.venv\Scripts\python.exe"

function Run-Stage([string]$Name, [string[]]$CliArgs) {
    Write-Host "`n=== $Name ===" -ForegroundColor Cyan
    $out = & $python -m pipeline.cli @CliArgs 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED" -ForegroundColor Red; Write-Host $out; exit 1
    }
    Write-Host $out.Trim()
    return $out
}

# S1 ingest -- returns JSON with batch_id
$raw = Run-Stage "S1 Ingest" @("ingest", "--source", $Source, $ZipPath)
$batchId = ($raw | ConvertFrom-Json).batch_id
Write-Host "batch_id = $batchId" -ForegroundColor Green

Run-Stage "S2 Parse & Index"     @("parse-index",       $batchId)
Run-Stage "S3 Quality Gate"      @("quality-gate",      $batchId)
Run-Stage "S4+S5 De-identify"    @("deidentify",        $batchId)
Run-Stage "S6 Volume Assembly"   @("assemble-volume",   $batchId)
Run-Stage "S7 Deface"            @("deface",            $batchId)
Run-Stage "S8 Segment"           @("segment",           $batchId)
Run-Stage "S9 Surface Extract"   @("extract-surface",   $batchId)
Run-Stage "S10 Mesh Postprocess" @("postprocess-mesh",  $batchId)
Run-Stage "S11 Export"           @("export",            $batchId)

Write-Host "`nPipeline complete!" -ForegroundColor Green
Write-Host "  batch_id : $batchId"
Write-Host "  API docs : http://localhost:8000/docs"
Write-Host "  MinIO    : http://localhost:9001  (browse gold bucket)"
