$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes

function Find-CaptionControl {
    $captionId = [Windows.Automation.PropertyCondition]::new(
        [Windows.Automation.AutomationElement]::AutomationIdProperty, 'CaptionsTextBlock')
    $desktop = [Windows.Automation.AutomationElement]::RootElement
    $windowClass = [Windows.Automation.PropertyCondition]::new(
        [Windows.Automation.AutomationElement]::ClassNameProperty, 'LiveCaptionsDesktopWindow')
    $captionWindow = $desktop.FindFirst([Windows.Automation.TreeScope]::Children, $windowClass)
    if ($null -ne $captionWindow) {
        return $captionWindow.FindFirst([Windows.Automation.TreeScope]::Descendants, $captionId)
    }

    $processes = Get-Process -Name LiveCaptions -ErrorAction SilentlyContinue |
        Where-Object SessionId -EQ ([Diagnostics.Process]::GetCurrentProcess().SessionId)
    foreach ($process in $processes) {
        $processId = [Windows.Automation.PropertyCondition]::new(
            [Windows.Automation.AutomationElement]::ProcessIdProperty, $process.Id)
        foreach ($window in $desktop.FindAll([Windows.Automation.TreeScope]::Children, $processId)) {
            $control = $window.FindFirst([Windows.Automation.TreeScope]::Descendants, $captionId)
            if ($null -ne $control) {
                return $control
            }
        }
    }
}

$control = $null
$lastState = ''
$lastCaption = ''
while ($true) {
    try {
        if ($null -eq $control) {
            $control = Find-CaptionControl
        }
        $state = 'waiting'
        $caption = ''
        if ($null -ne $control) {
            $caption = [string]$control.Current.Name
            $state = 'connected'
        }
    } catch [Windows.Automation.ElementNotAvailableException], [Runtime.InteropServices.COMException] {
        $control = $null
        $state = 'waiting'
        $caption = ''
    }

    if ($state -ne $lastState -or $caption -cne $lastCaption) {
        $event = @{state = $state; text = $caption} | ConvertTo-Json -Compress
        [Console]::WriteLine($event)
        $lastState = $state
        $lastCaption = $caption
    }
    if ($null -eq $control) {
        Start-Sleep -Milliseconds 750
    } else {
        Start-Sleep -Milliseconds 100
    }
}
