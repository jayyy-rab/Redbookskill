$ErrorActionPreference = 'Stop'
$profileDir = 'C:\Users\lyh17\AppData\Local\Microsoft\Edge\UserData'
$cdpPort = 9222

# Kill existing Edge
Get-Process msedge -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep 2

$edge = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
$argList = @(
    "--remote-debugging-port=$cdpPort",
    "--user-data-dir=$profileDir",
    '--no-first-run',
    '--no-default-browser-check',
    'https://www.xiaohongshu.com'
)

$proc = Start-Process $edge -ArgumentList $argList -PassThru
Write-Host "Edge PID: $($proc.Id)"

# Poll CDP until ready (max 20s)
$wsUrl = $null
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep 1
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:$cdpPort/json/version" -UseBasicParsing -TimeoutSec 2
        $data = $r.Content | ConvertFrom-Json
        $wsUrl = $data.webSocketDebuggerUrl
        Write-Host "CDP Ready: $($data.Browser)"
        Write-Host "WS URL: $wsUrl"
        break
    } catch {
        Write-Host "  waiting... ($i)"
    }
}

if (-not $wsUrl) {
    Write-Host "CDP FAILED TO START"
    exit 1
}

# Save WS URL
$wsFile = 'C:\Users\lyh17\.agents\skills\redbookskills\scripts\edge_ws_url.txt'
$wsUrl | Out-File -FilePath $wsFile -Encoding UTF8
Write-Host "WS URL saved to: $wsFile"
Write-Host "SUCCESS"
