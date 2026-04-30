$ErrorActionPreference = 'Continue'
$edge = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
$argList = @(
    '--remote-debugging-port=9222',
    '--user-data-dir=C:\Users\lyh17\AppData\Local\Microsoft\Edge\UserData',
    '--no-first-run',
    '--no-default-browser-check',
    'https://www.xiaohongshu.com'
)
$proc = Start-Process $edge -ArgumentList $argList -PassThru -WindowStyle Hidden
$proc.Id
Start-Sleep 6
try {
    $r = Invoke-WebRequest -Uri 'http://localhost:9222/json/version' -UseBasicParsing -TimeoutSec 3
    $r.Content
} catch {
    Write-Host "CDP not ready: $_"
}
