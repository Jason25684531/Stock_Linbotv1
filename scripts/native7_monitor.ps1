# Native-7 run monitor: every 60 s record process / result / error state; exit on the first change worth acting on.
# Exit reasons: TRACE_RESULT (a NaN trace wrote its JSON), RESULT_WRITTEN (a rules-only JSON changed), PROCESS_EXITED, ERROR_LOGGED, CAP_REACHED.
# Read-only: never starts, stops or restarts anything.
param([int]$MaxMinutes = 240, [int]$Interval = 60)
$out = 'D:\01_Project\Stocke\Stock_Linbotv1\outputs\dual_track_validation\2026-10-23'
$state = Join-Path $out 'native7_monitor_state.jsonl'
function Snapshot {
    $procs = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'native7_(rules_only|nan_trace)\.py' -and $_.ParentProcessId -notin (Get-CimInstance Win32_Process -Filter "Name='python.exe'").ProcessId })
    $files = Get-ChildItem $out -File | Where-Object { $_.Name -match '^native7_(rules_only_(prices2|flows2)|nan_trace_).*\.json$|^native7_(prices2|flows2|trace_.*)\.err$' }
    [pscustomobject]@{
        time = (Get-Date).ToString('s'); running = @($procs | ForEach-Object { ($_.CommandLine -split 'scripts/')[1].Trim() })
        # .err files stay locked by the writing process: tracked by size; result JSON by SHA-256
        files = @($files | ForEach-Object { @{ name = $_.Name; bytes = $_.Length; sha256 = $(if ($_.Extension -eq '.json') { (Get-FileHash $_.FullName -Algorithm SHA256).Hash } else { "size:$($_.Length)" }) } })
    }
}
$start = Snapshot
$start | ConvertTo-Json -Compress -Depth 4 | Add-Content $state
$deadline = (Get-Date).AddMinutes($MaxMinutes)
$now = $start
while ((Get-Date) -lt $deadline) {
    Start-Sleep $Interval
    $now = Snapshot
    $now | ConvertTo-Json -Compress -Depth 4 | Add-Content $state
    $before = @{}; $start.files | ForEach-Object { $before[$_.name] = $_.sha256 }
    $changed = @($now.files | Where-Object { $before[$_.name] -ne $_.sha256 })
    $reason = if ($changed | Where-Object { $_.name -like 'native7_nan_trace_*' }) { 'TRACE_RESULT' }
              elseif ($changed | Where-Object { $_.name -like '*.err' -and $_.bytes -gt 0 }) { 'ERROR_LOGGED' }
              elseif ($changed | Where-Object { $_.name -like 'native7_rules_only_*' }) { 'RESULT_WRITTEN' }
              elseif ($now.running.Count -lt $start.running.Count) { 'PROCESS_EXITED' }
    if ($reason) { "STOP $reason changed=$(($changed.name) -join ',') running=$($now.running.Count)"; exit 0 }
}
"STOP CAP_REACHED running=$($now.running.Count)"
