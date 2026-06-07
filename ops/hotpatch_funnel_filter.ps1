# Hot-patch funnel filter onto the live Railway container.
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

function Invoke-RailwayPython([string]$Code) {
    railway ssh "python -c `"$Code`""
    if ($LASTEXITCODE -ne 0) { throw "railway ssh failed" }
}

function Send-File([string]$LocalPath, [string]$RemotePath) {
    $bytes = [IO.File]::ReadAllBytes((Resolve-Path $LocalPath))
    $b64 = [Convert]::ToBase64String($bytes)
    Invoke-RailwayPython "open('$RemotePath','wb').close()"
    for ($i = 0; $i -lt $b64.Length; $i += 3000) {
        $len = [Math]::Min(3000, $b64.Length - $i)
        $chunk = $b64.Substring($i, $len)
        Invoke-RailwayPython "import base64; open('$RemotePath','ab').write(base64.b64decode('$chunk'))"
    }
    Write-Host "wrote $RemotePath ($($bytes.Length) bytes)"
}

Send-File "market_funnel.py" "/app/market_funnel.py"
Send-File "routers\funnel.py" "/app/routers/funnel.py"

$restart = "import os,signal,time,subprocess; pids=[]; "
$restart += "[pids.append(int(n)) for n in os.listdir('/proc') if n.isdigit() and 'uvicorn' in open(f'/proc/{n}/cmdline','rb').read().decode('latin-1') and 'market_server' in open(f'/proc/{n}/cmdline','rb').read().decode('latin-1')]; "
$restart += "[(os.kill(pid,signal.SIGTERM), print('stopped', pid)) for pid in pids]; time.sleep(1); "
$restart += "port=os.environ.get('PORT','8765'); subprocess.Popen(['python','-m','uvicorn','market_server:app','--host','0.0.0.0','--port',port], cwd='/app', stdout=open('/tmp/uvicorn-reload.log','ab'), stderr=subprocess.STDOUT, start_new_session=True); print('started', port)"
Invoke-RailwayPython $restart
Write-Host "hotpatch complete"