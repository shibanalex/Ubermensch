# Регистрирует автозапуск Übermensch при входе в Windows (без окон консоли):
# ядро (API + Telegram) и приложение с котиком.
# Удалить: Unregister-ScheduledTask -TaskName "Ubermensch*" -Confirm:$false
$project = Split-Path -Parent $PSScriptRoot
$pythonw = "$project\.venv\Scripts\pythonw.exe"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1)

$tasks = @{
    "Ubermensch"         = @{ Module = "ubermensch";         Description = "Übermensch: ядро" }
    "Ubermensch Desktop" = @{ Module = "ubermensch.desktop"; Description = "Übermensch: котик и лист задач" }
}
foreach ($name in $tasks.Keys) {
    $action = New-ScheduledTaskAction -Execute $pythonw -Argument "-m $($tasks[$name].Module)" `
        -WorkingDirectory $project
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
        -Description $tasks[$name].Description -Force | Out-Null
}
Write-Host "Автозапуск установлен. Запустить сейчас: Start-ScheduledTask -TaskName Ubermensch; Start-ScheduledTask -TaskName 'Ubermensch Desktop'"
