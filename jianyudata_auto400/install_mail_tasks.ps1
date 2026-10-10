$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$pythonw = 'C:\Users\Administrator\AppData\Local\Programs\Python\Python314\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw)) {
    throw "未找到 pythonw：$pythonw"
}

function Register-JianyuDailyTask([string]$Name, [string]$Script, [string]$Arguments, [datetime]$At) {
    $allArguments = '"' + (Join-Path $root $Script) + '" ' + $Arguments
    $action = New-ScheduledTaskAction -Execute $pythonw -Argument $allArguments -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -Daily -At $At
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
}

Register-JianyuDailyTask 'Jianyu-Mail-Watchdog-1050' 'daily_mail_watchdog.py' '--mode download' ([datetime]'10:50')
Register-JianyuDailyTask 'Jianyu-Mail-Watchdog-1120' 'daily_mail_watchdog.py' '--mode combined' ([datetime]'11:20')

$retryScript = Join-Path $root 'run_mail_retry.py'
$retryCommand = '"' + $pythonw + '" "' + $retryScript + '"'
& schtasks.exe /Create /TN 'Jianyu-Mail-Retry' /TR $retryCommand /SC MINUTE /MO 5 /F | Out-Null
if ($LASTEXITCODE -ne 0) { throw '注册 Jianyu-Mail-Retry 失败' }

Get-ScheduledTask -TaskName 'Jianyu-Mail-*' | ForEach-Object {
    $info = $_ | Get-ScheduledTaskInfo
    [pscustomobject]@{ TaskName=$_.TaskName; State=$_.State; NextRunTime=$info.NextRunTime }
}
