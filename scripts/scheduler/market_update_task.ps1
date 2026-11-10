<#
.SYNOPSIS
  Windows task for the daily market data update (change 2026-11-09-autonomous-data-readiness-v1, design D7).

.DESCRIPTION
  Owner-run. The agent only runs -DryRun. Actions: install | enable | disable | remove | status.
  install is idempotent: an identical task returns UNCHANGED; a same-named task with any other setting is REFUSED with the
  differences and is never overwritten (no -Force). A Stock/Linbot task, or any task whose action names this repository,
  triggering within -ConflictMinutes of -Time is REFUSED. Output is one JSON object; exit 0 = done or unchanged, 2 = refused.
  The task sets STOCK_LINBOT_TRIGGER=scheduled so `python -m services.market_data_update --verify-scheduled` can tell an
  automatic run from a manual one. Without -Password the task runs only while the account is logged on.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\scheduler\market_update_task.ps1 -Action install -DryRun
#>
param(
    [Parameter(Mandatory = $true)][ValidateSet('install', 'enable', 'disable', 'remove', 'status')][string]$Action,
    [string]$TaskName = 'StockLinbot_MarketDataUpdate',
    [ValidatePattern('^\d{2}:\d{2}$')][string]$Time = '17:00',
    [int]$ConflictMinutes = 60,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Python = Join-Path $Repo 'myenv\Scripts\python.exe'
$Log = Join-Path $Repo 'outputs\market_data_updates\scheduler.log'
$Arguments = "/c set STOCK_LINBOT_TRIGGER=scheduled&& `"$Python`" -m services.market_data_update --daily >> `"$Log`" 2>&1"
$Want = [ordered]@{ Execute = 'cmd.exe'; Arguments = $Arguments; WorkingDirectory = $Repo; Time = $Time }

function Out-Result($Status, $Extra = @{}) {
    $body = [ordered]@{ action = $Action; task = $TaskName; status = $Status; dry_run = [bool]$DryRun }
    foreach ($key in $Extra.Keys) { $body[$key] = $Extra[$key] }
    $body | ConvertTo-Json -Depth 5
}

function Get-Existing { Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue }

function Get-Conflicts {
    $target = [datetime]::ParseExact($Time, 'HH:mm', $null).TimeOfDay
    foreach ($task in Get-ScheduledTask) {
        if ($task.TaskName -eq $TaskName) { continue }
        $related = $task.TaskName -match 'Stock|Linbot' -or (($task.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments) $($_.WorkingDirectory)" }) -join ' ') -like "*$Repo*"
        if (-not $related) { continue }
        foreach ($trigger in $task.Triggers) {
            if (-not $trigger.StartBoundary) { continue }
            $at = ([datetime]$trigger.StartBoundary).TimeOfDay
            $gap = [math]::Abs(($at - $target).TotalMinutes)
            $gap = [math]::Min($gap, 1440 - $gap)
            $repeats = $trigger.Repetition -and $trigger.Repetition.Interval
            if ($gap -le $ConflictMinutes -or $repeats) {
                [ordered]@{ task = $task.TaskName; trigger = $at.ToString(); repeats = [bool]$repeats; minutes_apart = [int]$gap }
            }
        }
    }
}

switch ($Action) {
    'install' {
        $existing = Get-Existing
        if ($existing) {
            $a = $existing.Actions[0]
            $t = $existing.Triggers[0]
            $have = [ordered]@{ Execute = $a.Execute; Arguments = $a.Arguments; WorkingDirectory = $a.WorkingDirectory
                Time = if ($t.StartBoundary) { ([datetime]$t.StartBoundary).ToString('HH:mm') } else { $null } }
            $diff = @($Want.Keys | Where-Object { $have[$_] -ne $Want[$_] } | ForEach-Object { [ordered]@{ field = $_; existing = $have[$_]; wanted = $Want[$_] } })
            if ($diff.Count -eq 0) { Out-Result 'UNCHANGED'; exit 0 }
            Out-Result 'REFUSED_DIFFERENT_EXISTING_TASK' @{ differences = $diff; note = 'not overwritten; remove or adjust it yourself' }
            exit 2
        }
        $conflicts = @(Get-Conflicts)
        if ($conflicts.Count -gt 0) { Out-Result 'REFUSED_SCHEDULE_CONFLICT' @{ conflicts = $conflicts }; exit 2 }
        if ($DryRun) { Out-Result 'PLANNED_REGISTER' @{ wanted = $Want }; exit 0 }
        $taskAction = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument $Arguments -WorkingDirectory $Repo
        $trigger = New-ScheduledTaskTrigger -Daily -At $Time
        Register-ScheduledTask -TaskName $TaskName -Action $taskAction -Trigger $trigger -User "$env:USERDOMAIN\$env:USERNAME" -RunLevel Limited | Out-Null
        Out-Result 'REGISTERED' @{ wanted = $Want; next = 'run it once with schtasks /Run, then verify on the next session' }
    }
    { $_ -in 'enable', 'disable', 'remove' } {
        if (-not (Get-Existing)) { Out-Result 'NOT_FOUND'; exit 2 }
        if ($DryRun) { Out-Result "PLANNED_$($Action.ToUpper())"; exit 0 }
        switch ($Action) {
            'enable' { Enable-ScheduledTask -TaskName $TaskName | Out-Null }
            'disable' { Disable-ScheduledTask -TaskName $TaskName | Out-Null }
            'remove' { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false }
        }
        Out-Result 'DONE'
    }
    'status' {
        $existing = Get-Existing
        if (-not $existing) { Out-Result 'NOT_FOUND' @{ scheduler_state = 'SCHEDULER_SETUP_REQUIRED' }; exit 0 }
        $info = Get-ScheduledTaskInfo -TaskName $TaskName
        Out-Result 'FOUND' @{ state = "$($existing.State)"; last_run = "$($info.LastRunTime)"; last_result = $info.LastTaskResult
            next_run = "$($info.NextRunTime)"; note = 'status only; acceptance is --verify-scheduled after the next session' }
    }
}
