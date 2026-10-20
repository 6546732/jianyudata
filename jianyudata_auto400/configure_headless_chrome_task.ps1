param(
    [string]$TaskName = 'Jianyu-Automation-Chrome',
    [switch]$ValidateOnly
)

# Configure the existing persistent Jianyu Chrome task for headless operation.
# This script preserves the task trigger, user, profile, and other settings. It
# never starts Chrome or the export job. The next scheduled trigger replaces
# the old task instance because the instance policy is set to StopExisting.
$ErrorActionPreference = 'Stop'

$service = New-Object -ComObject 'Schedule.Service'
$service.Connect()
$folder = $service.GetFolder('\')
$task = $folder.GetTask($TaskName)
$definition = $task.Definition

if (@($definition.Actions).Count -ne 1) {
    throw "Task $TaskName must have exactly one action."
}

$action = $definition.Actions.Item(1)
if ($action.Type -ne 0 -or [IO.Path]::GetFileName($action.Path) -ine 'chrome.exe') {
    throw "Task $TaskName is not a single Chrome executable action."
}

$arguments = [string]$action.Arguments
if ($arguments -notmatch '--user-data-dir(?:=|\s)') {
    throw 'The Chrome action has no dedicated user-data-dir; refusing to change an unrelated browser task.'
}
if ($arguments -notmatch '--remote-debugging-port(?:=|\s)') {
    throw 'The Chrome action has no remote debugging port; refusing to change an unrelated browser task.'
}

# Replace any prior headless mode flag, then add exactly the tested new mode.
$arguments = [regex]::Replace($arguments, '(?<!\S)--headless(?:=[^\s]+)?\s*', '').Trim()
$action.Arguments = '--headless=new ' + $arguments
$definition.Settings.MultipleInstances = 3 # TASK_INSTANCES_STOP_EXISTING

if ($ValidateOnly) {
    [pscustomobject]@{
        TaskName = $TaskName
        Chrome = $action.Path
        Arguments = $action.Arguments
        MultipleInstancesPolicy = 'StopExisting'
        NextRunTime = $task.NextRunTime
        Changed = $false
    }
    return
}

$backupDirectory = Join-Path $env:LOCALAPPDATA (
    'JianyuTaskBackups\headless-' + (Get-Date -Format 'yyyyMMddHHmmss'))
New-Item -ItemType Directory -Path $backupDirectory -Force | Out-Null
$backupPath = Join-Path $backupDirectory ($TaskName + '.xml')
[IO.File]::WriteAllText($backupPath, $task.Xml, [Text.Encoding]::Unicode)

$principal = $definition.Principal
$folder.RegisterTaskDefinition(
    $TaskName,
    $definition,
    6, # TASK_CREATE_OR_UPDATE
    $principal.UserId,
    $null,
    [int]$principal.LogonType,
    $null
) | Out-Null

$updated = $folder.GetTask($TaskName)
$updatedXml = [xml]$updated.Xml
$updatedArguments = [string]$updatedXml.Task.Actions.Exec.Arguments
$updatedPolicy = [string]$updatedXml.Task.Settings.MultipleInstancesPolicy
if ($updatedArguments -notmatch '(?:^|\s)--headless=new(?:\s|$)' -or
    $updatedPolicy -ne 'StopExisting') {
    throw 'Task registration completed, but the updated headless settings did not verify.'
}

[pscustomobject]@{
    TaskName = $TaskName
    State = $updated.State
    NextRunTime = $updated.NextRunTime
    Arguments = $updatedArguments
    MultipleInstancesPolicy = $updatedPolicy
    WakeToRun = $updatedXml.Task.Settings.WakeToRun
    StartWhenAvailable = $updatedXml.Task.Settings.StartWhenAvailable
    Backup = $backupPath
    StartedTask = $false
}
