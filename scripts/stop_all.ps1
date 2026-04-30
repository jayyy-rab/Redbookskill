$ErrorActionPreference = "SilentlyContinue"

Write-Host "[stop_all] Stopping redbookskills python processes..."

$targets = Get-CimInstance Win32_Process |
  Where-Object {
    $_.Name -like "python*" -and
    $_.CommandLine -like "*redbookskills*"
  }

if (-not $targets) {
  Write-Host "[stop_all] No matching process found."
  exit 0
}

$count = 0
foreach ($p in $targets) {
  try {
    Stop-Process -Id $p.ProcessId -Force
    $count++
  } catch {
    # ignore
  }
}

Write-Host "[stop_all] Stopped $count process(es)."
