param(
    [switch]$Execute,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$python = 'C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe'
$script = Join-Path $PSScriptRoot 'sync_to_salesforce.py'
$common = @($script, '--base', 'D:\桌面\data', '--target-org', 'sfoa-prod')
$preflightArguments = @($common + '--dry-run')

if ($Force) {
    $preflightArguments += '--force'
}

Write-Host '生产预演：只读核对字段及重复数据，不写入 Salesforce。'
$preflightText = (& $python @preflightArguments | Out-String)
if ($LASTEXITCODE -ne 0) {
    Write-Output $preflightText
    exit $LASTEXITCODE
}
$preflight = $preflightText | ConvertFrom-Json
Write-Output $preflightText

if (-not $Execute) { exit 0 }
if (-not $preflight.safe_to_execute) {
    Write-Host '生产预演未通过，已阻止正式上传。请先补齐字段并处理无外部键/重复记录。' -ForegroundColor Red
    exit 2
}

Write-Host '生产预演已通过：按 Source_Key__c 执行 Bulk Upsert。'
$runner = Join-Path $PSScriptRoot 'run_production_sync.py'
$arguments = @($runner)
if ($Force) { $arguments += '--force' }
& $python @arguments
exit $LASTEXITCODE
