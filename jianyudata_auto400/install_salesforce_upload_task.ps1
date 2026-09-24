# 新建或更新独立 Salesforce 上传任务；不启动任务，不访问剑鱼网站。
$ErrorActionPreference = 'Stop'
$sourceName = 'Jianyu-Formal-1030'
$taskName = 'Jianyu-Salesforce-Sync-1100'
$source = Get-ScheduledTask -TaskName $sourceName
if (@($source.Actions).Count -ne 1) {
    throw "任务 $sourceName 必须有且仅有一个操作，无法安全继承 Python 配置。"
}
$python = $source.Actions[0].Execute
if (-not $python) {
    throw "任务 $sourceName 没有可复用的 Python 可执行文件。"
}
$sourceArguments = [string]$source.Actions[0].Arguments
$sourceScript = $null
if ($sourceArguments -match '"([^"]*run_scheduled_notebook\.py)"') {
    $sourceScript = $Matches[1]
}
$runtimeDirectory = if ($sourceScript) {
    Split-Path -Parent $sourceScript
} else {
    $PSScriptRoot
}
$script = Join-Path $runtimeDirectory 'run_salesforce_sync.py'
if (-not (Test-Path -LiteralPath $script)) {
    throw "上传入口不存在：$script"
}
$action = New-ScheduledTaskAction -Execute $python `
    -Argument ('"' + $script + '"') -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -Daily -At '11:00'
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$parameters = @{
    TaskName = $taskName
    Action = $action
    Trigger = $trigger
    Settings = $settings
    Principal = $source.Principal
    Force = $true
}
Register-ScheduledTask @parameters | Out-Null
$info = Get-ScheduledTaskInfo -TaskName $taskName
[pscustomobject]@{
    TaskName = $taskName
    NextRunTime = $info.NextRunTime
    Script = $script
    Python = $python
}
