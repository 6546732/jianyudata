# 更新正式任务到每天10:29/10:30，不立即启动浏览器或执行导出。
$ErrorActionPreference = 'Stop'
$times = @{
    'Jianyu-Automation-Chrome' = '10:29:00+08:00'
    'Jianyu-Formal-1030' = '10:30:00+08:00'
}
# 先验证两个任务，避免任务缺失时只修改一个。
$tasks = @{}
foreach ($name in $times.Keys) {
    $tasks[$name] = Get-ScheduledTask -TaskName $name
    $daily = @($tasks[$name].Triggers | Where-Object {
        $_.CimClass.CimClassName -eq 'MSFT_TaskDailyTrigger'
    })
    if ($daily.Count -ne 1) {
        throw "任务 $name 必须有且仅有一个每日触发器。"
    }
}
$backup = Join-Path $env:LOCALAPPDATA (
    'JianyuTaskBackups\' + (Get-Date -Format 'yyyyMMddHHmmss'))
New-Item -ItemType Directory -Path $backup -Force | Out-Null
foreach ($name in $times.Keys) {
    Export-ScheduledTask -TaskName $name |
        Set-Content -LiteralPath (Join-Path $backup ($name + '.xml')) -Encoding utf8
}
$beijingDate = [DateTimeOffset]::UtcNow.ToOffset(
    [TimeSpan]::FromHours(8)).ToString('yyyy-MM-dd')
foreach ($name in $times.Keys) {
    foreach ($trigger in $tasks[$name].Triggers) {
        if ($trigger.CimClass.CimClassName -eq 'MSFT_TaskDailyTrigger') {
            $trigger.StartBoundary = $beijingDate + 'T' + $times[$name]
        }
    }
    Set-ScheduledTask -TaskName $name -Trigger $tasks[$name].Triggers | Out-Null
    Get-ScheduledTaskInfo -TaskName $name | Select-Object TaskName, NextRunTime
}
Write-Output "原任务定义备份：$backup"
