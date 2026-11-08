# 在项目独立环境中启动回环服务；不读取 .env 或发起模型下载。
$ErrorActionPreference = 'Stop'
$deciderWorkspace = Split-Path -Parent $PSScriptRoot
$deciderPython = Join-Path $deciderWorkspace 'data/runtimes/decider/Scripts/python.exe'
$deciderLogDirectory = Join-Path $deciderWorkspace 'data/reports/rss-triage'
if (-not (Test-Path -LiteralPath $deciderPython)) { throw '请先安装 Decider 独立环境和官方权重。' }
if (Get-NetTCPConnection -LocalPort 8895 -State Listen -ErrorAction SilentlyContinue) {
    Write-Output '8895 端口已被占用；没有启动第二个 GPU 服务。请先核对 /health 模型版本。'
    exit 0
}
New-Item -ItemType Directory -Force -Path $deciderLogDirectory | Out-Null
$deciderProcess = Start-Process -FilePath $deciderPython -ArgumentList @('scripts/serve_decider.py') `
    -WorkingDirectory $deciderWorkspace -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $deciderLogDirectory 'model.stdout.log') `
    -RedirectStandardError (Join-Path $deciderLogDirectory 'model.stderr.log')
Write-Output "Decider 启动进程 PID=$($deciderProcess.Id)；权重加载后监听 http://127.0.0.1:8895。"
