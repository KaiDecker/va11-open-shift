$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Security
[Windows.Forms.Application]::EnableVisualStyles()

$packageRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$installDir = Join-Path $env:LOCALAPPDATA "OpenShift"
$gameCopyDir = Join-Path $installDir "game"
$script:activeProcess = $null
$script:activeMode = ""
$script:activeLog = ""
$script:lastLog = ""
$script:pendingKey = ""
$script:completionMarker = ""
$script:launchConfirmed = $false

function Find-SteamGame {
    $roots = @(
        (Join-Path ${env:ProgramFiles(x86)} "Steam"),
        (Join-Path $env:ProgramFiles "Steam"),
        "C:\Steam"
    ) | Where-Object { $_ }
    $libraries = New-Object Collections.Generic.List[string]
    foreach ($root in $roots) {
        $libraries.Add($root)
        $vdf = Join-Path $root "steamapps\libraryfolders.vdf"
        if (Test-Path -LiteralPath $vdf) {
            $content = Get-Content -LiteralPath $vdf -Raw
            foreach ($match in [regex]::Matches($content, '"path"\s+"([^"]+)"')) {
                $libraries.Add($match.Groups[1].Value.Replace('\\', '\'))
            }
        }
    }
    foreach ($library in $libraries) {
        $candidate = Join-Path $library "steamapps\common\VA-11 HALL-A"
        if (Test-Path -LiteralPath (Join-Path $candidate "data.win") -PathType Leaf) {
            return [IO.Path]::GetFullPath($candidate)
        }
    }
    return ""
}

function Save-ApiKey([string] $value) {
    if ([string]::IsNullOrWhiteSpace($value)) { throw "API key cannot be empty." }
    New-Item -ItemType Directory -Force -Path $installDir | Out-Null
    $bytes = [Text.Encoding]::UTF8.GetBytes($value.Trim())
    try {
        $protected = [System.Security.Cryptography.ProtectedData]::Protect(
            $bytes,
            $null,
            [System.Security.Cryptography.DataProtectionScope]::CurrentUser
        )
        [IO.File]::WriteAllBytes((Join-Path $installDir "api-key.dpapi"), $protected)
    } finally {
        [Array]::Clear($bytes, 0, $bytes.Length)
    }
}

function Quote-Argument([string] $value) {
    return '"' + $value.Replace('"', '\"') + '"'
}

function New-RoundedRegion([int] $width, [int] $height, [int] $radius) {
    $path = New-Object Drawing.Drawing2D.GraphicsPath
    $diameter = $radius * 2
    $path.AddArc(0, 0, $diameter, $diameter, 180, 90)
    $path.AddArc($width - $diameter, 0, $diameter, $diameter, 270, 90)
    $path.AddArc($width - $diameter, $height - $diameter, $diameter, $diameter, 0, 90)
    $path.AddArc(0, $height - $diameter, $diameter, $diameter, 90, 90)
    $path.CloseFigure()
    return New-Object Drawing.Region($path)
}

function Start-HiddenPowerShell([string] $script, [string[]] $arguments, [string] $log) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $log) | Out-Null
    Remove-Item -LiteralPath $log -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath ($log + ".error") -Force -ErrorAction SilentlyContinue
    $argumentList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Quote-Argument $script))
    $argumentList += $arguments | ForEach-Object { Quote-Argument $_ }
    return Start-Process -FilePath "powershell.exe" `
        -ArgumentList ($argumentList -join " ") `
        -WindowStyle Hidden `
        -RedirectStandardOutput $log `
        -RedirectStandardError ($log + ".error") `
        -PassThru
}

$form = New-Object Windows.Forms.Form
$form.Text = "OPEN SHIFT · 安装器"
$form.ClientSize = New-Object Drawing.Size(720, 535)
$form.FormBorderStyle = "FixedSingle"
$form.MaximizeBox = $false
$form.StartPosition = "CenterScreen"
$form.Font = New-Object Drawing.Font("Segoe UI", 10)
$form.BackColor = [Drawing.Color]::FromArgb(8, 12, 29)
$iconPath = Join-Path $packageRoot "OpenShift.ico"
if (Test-Path -LiteralPath $iconPath -PathType Leaf) {
    $form.Icon = New-Object Drawing.Icon($iconPath)
}

$headerPanel = New-Object Windows.Forms.Panel
$headerPanel.BackColor = [Drawing.Color]::FromArgb(14, 22, 43)
$headerPanel.SetBounds(0, 0, 720, 94)
$form.Controls.Add($headerPanel)

$headerRule = New-Object Windows.Forms.Panel
$headerRule.BackColor = [Drawing.Color]::FromArgb(68, 202, 224)
$headerRule.SetBounds(24, 86, 150, 3)
$form.Controls.Add($headerRule)

$title = New-Object Windows.Forms.Label
$title.Text = "OPEN SHIFT"
$title.Font = New-Object Drawing.Font("Segoe UI", 22, [Drawing.FontStyle]::Bold)
$title.ForeColor = [Drawing.Color]::FromArgb(244, 248, 255)
$title.SetBounds(24, 20, 300, 42)
$form.Controls.Add($title)

$subtitle = New-Object Windows.Forms.Label
$subtitle.Text = "VA-11 HALL-A 结局之后的安全安装与启动工具"
$subtitle.ForeColor = [Drawing.Color]::FromArgb(148, 174, 201)
$subtitle.SetBounds(28, 63, 520, 26)
$form.Controls.Add($subtitle)

$contentPanel = New-Object Windows.Forms.Panel
$contentPanel.BackColor = [Drawing.Color]::FromArgb(14, 22, 43)
$contentPanel.BorderStyle = "FixedSingle"
$contentPanel.SetBounds(18, 104, 684, 292)
$form.Controls.Add($contentPanel)

$steamLabel = New-Object Windows.Forms.Label
$steamLabel.Text = "Steam 游戏目录"
$steamLabel.ForeColor = [Drawing.Color]::FromArgb(219, 231, 246)
$steamLabel.SetBounds(34, 113, 180, 24)
$form.Controls.Add($steamLabel)

$steamBox = New-Object Windows.Forms.TextBox
$steamBox.Text = Find-SteamGame
$steamBox.BackColor = [Drawing.Color]::FromArgb(8, 14, 29)
$steamBox.ForeColor = [Drawing.Color]::FromArgb(241, 246, 255)
$steamBox.BorderStyle = "FixedSingle"
$steamBox.SetBounds(34, 140, 549, 30)
$form.Controls.Add($steamBox)

$browseButton = New-Object Windows.Forms.Button
$browseButton.Text = "浏览..."
$browseButton.SetBounds(590, 138, 96, 34)
$form.Controls.Add($browseButton)

$copyLabel = New-Object Windows.Forms.Label
$copyLabel.Text = "隔离游戏副本：" + $gameCopyDir
$copyLabel.ForeColor = [Drawing.Color]::FromArgb(128, 157, 185)
$copyLabel.SetBounds(34, 177, 650, 24)
$form.Controls.Add($copyLabel)

$keyLabel = New-Object Windows.Forms.Label
$keyLabel.Text = "DeepSeek API Key"
$keyLabel.ForeColor = [Drawing.Color]::FromArgb(219, 231, 246)
$keyLabel.SetBounds(34, 216, 180, 24)
$form.Controls.Add($keyLabel)

$keyBox = New-Object Windows.Forms.TextBox
$keyBox.UseSystemPasswordChar = $true
$keyBox.BackColor = [Drawing.Color]::FromArgb(8, 14, 29)
$keyBox.ForeColor = [Drawing.Color]::FromArgb(241, 246, 255)
$keyBox.BorderStyle = "FixedSingle"
$keyBox.SetBounds(34, 243, 549, 30)
$form.Controls.Add($keyBox)

$saveKeyButton = New-Object Windows.Forms.Button
$saveKeyButton.Text = "保存 Key"
$saveKeyButton.SetBounds(590, 241, 96, 34)
$form.Controls.Add($saveKeyButton)

$keyHint = New-Object Windows.Forms.Label
$keyHint.Text = "使用当前用户的 Windows DPAPI 加密，不写入 TOML、日志或存档。"
$keyHint.ForeColor = [Drawing.Color]::FromArgb(128, 157, 185)
$keyHint.SetBounds(34, 280, 650, 24)
$form.Controls.Add($keyHint)

$safetyPanel = New-Object Windows.Forms.Panel
$safetyPanel.BackColor = [Drawing.Color]::FromArgb(20, 31, 56)
$safetyPanel.BorderStyle = "FixedSingle"
$safetyPanel.SetBounds(34, 316, 652, 66)
$form.Controls.Add($safetyPanel)

$safety = New-Object Windows.Forms.Label
$safety.Text = "Steam 原版 data.win：只读输入`r`n补丁 data.win：仅写入隔离副本`r`n世界数据库和配对存档：%LOCALAPPDATA%\VA_11_Hall_A"
$safety.ForeColor = [Drawing.Color]::FromArgb(177, 202, 226)
$safety.SetBounds(12, 7, 620, 54)
$safetyPanel.Controls.Add($safety)

$actionPanel = New-Object Windows.Forms.Panel
$actionPanel.BackColor = [Drawing.Color]::FromArgb(14, 22, 43)
$actionPanel.SetBounds(18, 406, 684, 82)
$form.Controls.Add($actionPanel)

$installButton = New-Object Windows.Forms.Button
$installButton.Text = "安装 / 修复"
$installButton.SetBounds(34, 418, 140, 40)
$form.Controls.Add($installButton)

$startButton = New-Object Windows.Forms.Button
$startButton.Text = "准备并启动"
$startButton.SetBounds(184, 418, 160, 40)
$startButton.Enabled = Test-Path -LiteralPath (Join-Path $installDir "Start-Open-Shift.ps1")
$form.Controls.Add($startButton)

$logButton = New-Object Windows.Forms.Button
$logButton.Text = "打开日志"
$logButton.SetBounds(354, 418, 105, 40)
$form.Controls.Add($logButton)

$uninstallButton = New-Object Windows.Forms.Button
$uninstallButton.Text = "卸载"
$uninstallButton.SetBounds(581, 418, 105, 40)
$form.Controls.Add($uninstallButton)

$status = New-Object Windows.Forms.Label
$status.Text = "就绪。Steam 原版文件保持只读。"
$status.ForeColor = [Drawing.Color]::FromArgb(104, 224, 232)
$status.SetBounds(28, 494, 663, 26)
$form.Controls.Add($status)

$progress = New-Object Windows.Forms.ProgressBar
$progress.Style = "Marquee"
$progress.MarqueeAnimationSpeed = 0
$progress.SetBounds(28, 522, 663, 8)
$form.Controls.Add($progress)

function Set-ButtonStyle([Windows.Forms.Button] $button, [Drawing.Color] $background, [Drawing.Color] $foreground) {
    $button.FlatStyle = "Flat"
    $button.FlatAppearance.BorderSize = 1
    $button.FlatAppearance.BorderColor = [Drawing.Color]::FromArgb(65, 91, 124)
    $button.BackColor = $background
    $button.ForeColor = $foreground
    $button.Cursor = [Windows.Forms.Cursors]::Hand
}

Set-ButtonStyle $browseButton ([Drawing.Color]::FromArgb(24, 43, 72)) ([Drawing.Color]::FromArgb(215, 232, 247))
Set-ButtonStyle $saveKeyButton ([Drawing.Color]::FromArgb(24, 43, 72)) ([Drawing.Color]::FromArgb(215, 232, 247))
Set-ButtonStyle $installButton ([Drawing.Color]::FromArgb(35, 146, 166)) ([Drawing.Color]::White)
Set-ButtonStyle $startButton ([Drawing.Color]::FromArgb(226, 61, 139)) ([Drawing.Color]::White)
Set-ButtonStyle $logButton ([Drawing.Color]::FromArgb(24, 43, 72)) ([Drawing.Color]::FromArgb(215, 232, 247))
Set-ButtonStyle $uninstallButton ([Drawing.Color]::FromArgb(74, 34, 63)) ([Drawing.Color]::FromArgb(255, 194, 215))

$headerPanel.Region = New-RoundedRegion $headerPanel.Width $headerPanel.Height 18
$contentPanel.Region = New-RoundedRegion $contentPanel.Width $contentPanel.Height 16
$actionPanel.Region = New-RoundedRegion $actionPanel.Width $actionPanel.Height 16
$safetyPanel.Region = New-RoundedRegion $safetyPanel.Width $safetyPanel.Height 12
foreach ($field in @($steamBox, $keyBox)) {
    $field.BorderStyle = "None"
    $field.Region = New-RoundedRegion $field.Width $field.Height 8
}
foreach ($button in @($browseButton, $saveKeyButton, $installButton, $startButton, $logButton, $uninstallButton)) {
    $button.Region = New-RoundedRegion $button.Width $button.Height 10
}

function Set-ButtonHover([Windows.Forms.Button] $button, [Drawing.Color] $baseColor, [Drawing.Color] $hoverColor) {
    $button.Tag = [pscustomobject]@{ Base = $baseColor; Hover = $hoverColor }
    $button.Add_MouseEnter({ param($sender, $eventArgs); $sender.BackColor = $sender.Tag.Hover })
    $button.Add_MouseLeave({ param($sender, $eventArgs); $sender.BackColor = $sender.Tag.Base })
}

Set-ButtonHover $browseButton ([Drawing.Color]::FromArgb(24, 43, 72)) ([Drawing.Color]::FromArgb(35, 61, 96))
Set-ButtonHover $saveKeyButton ([Drawing.Color]::FromArgb(24, 43, 72)) ([Drawing.Color]::FromArgb(35, 61, 96))
Set-ButtonHover $installButton ([Drawing.Color]::FromArgb(35, 146, 166)) ([Drawing.Color]::FromArgb(53, 178, 193))
Set-ButtonHover $startButton ([Drawing.Color]::FromArgb(226, 61, 139)) ([Drawing.Color]::FromArgb(244, 84, 159))
Set-ButtonHover $logButton ([Drawing.Color]::FromArgb(24, 43, 72)) ([Drawing.Color]::FromArgb(35, 61, 96))
Set-ButtonHover $uninstallButton ([Drawing.Color]::FromArgb(74, 34, 63)) ([Drawing.Color]::FromArgb(105, 43, 83))

# Keep decorative panels behind the interactive controls in WinForms' z-order.
$headerPanel.SendToBack()
$headerRule.SendToBack()
$contentPanel.SendToBack()
$actionPanel.SendToBack()
$title.BringToFront()
$subtitle.BringToFront()
foreach ($control in @($steamLabel, $steamBox, $browseButton, $copyLabel, $keyLabel, $keyBox, $saveKeyButton, $keyHint, $safetyPanel, $installButton, $startButton, $logButton, $uninstallButton, $status, $progress)) {
    $control.BringToFront()
}

function Set-Busy([bool] $busy) {
    $installButton.Enabled = -not $busy
    $uninstallButton.Enabled = -not $busy
    $browseButton.Enabled = -not $busy
    $saveKeyButton.Enabled = -not $busy
    $steamBox.Enabled = -not $busy
    $keyBox.Enabled = -not $busy
    $startButton.Enabled = (-not $busy) -and (Test-Path -LiteralPath (Join-Path $installDir "Start-Open-Shift.ps1"))
    $progress.MarqueeAnimationSpeed = if ($busy) { 25 } else { 0 }
}

function Complete-Log([string] $log) {
    $errorLog = $log + ".error"
    if ((Test-Path -LiteralPath $errorLog -PathType Leaf) -and (Get-Item -LiteralPath $errorLog).Length -gt 0) {
        Add-Content -LiteralPath $log -Value "`r`n--- error output ---"
        Get-Content -LiteralPath $errorLog | Add-Content -LiteralPath $log
    }
}

$browseButton.Add_Click({
    $dialog = New-Object Windows.Forms.FolderBrowserDialog
    $dialog.Description = "选择包含 data.win 的 VA-11 HALL-A Steam 游戏目录"
    if ($dialog.ShowDialog() -eq "OK") { $steamBox.Text = $dialog.SelectedPath }
})

$saveKeyButton.Add_Click({
    try {
        Save-ApiKey $keyBox.Text
        $keyBox.Clear()
        $status.Text = "DeepSeek API Key 已为当前 Windows 用户加密保存。"
    } catch {
        [Windows.Forms.MessageBox]::Show($_.Exception.Message, "Open Shift", "OK", "Error") | Out-Null
    }
})

$installButton.Add_Click({
    $steam = $steamBox.Text.Trim()
    if (-not (Test-Path -LiteralPath (Join-Path $steam "data.win") -PathType Leaf)) {
        [Windows.Forms.MessageBox]::Show("请选择包含 data.win 的 VA-11 HALL-A Steam 游戏目录。", "Open Shift", "OK", "Warning") | Out-Null
        return
    }
    try {
        $script:pendingKey = $keyBox.Text
        $log = Join-Path $env:TEMP "open-shift-install.log"
        $marker = Join-Path $env:TEMP ("open-shift-install-" + [guid]::NewGuid().ToString("N") + ".complete")
        $script:activeProcess = Start-HiddenPowerShell `
            (Join-Path $packageRoot "packaging\install-open-shift.ps1") `
            @("-SteamGameDir", $steam, "-InstallDir", $installDir, "-GameCopyDir", $gameCopyDir, "-CompletionMarker", $marker, "-SkipCredential") `
            $log
        $script:activeMode = "install"
        $script:activeLog = $log
        $script:lastLog = $log
        $script:completionMarker = $marker
        Set-Busy $true
        $status.Text = "正在校验 Steam 文件并生成隔离副本..."
    } catch {
        $script:activeProcess = $null
        Set-Busy $false
        [Windows.Forms.MessageBox]::Show($_.Exception.Message, "Open Shift", "OK", "Error") | Out-Null
    }
})

$startButton.Add_Click({
    $launcher = Join-Path $installDir "Start-Open-Shift.ps1"
    if (-not (Test-Path -LiteralPath $launcher)) {
        [Windows.Forms.MessageBox]::Show("请先安装 Open Shift。", "Open Shift", "OK", "Warning") | Out-Null
        return
    }
    try {
        $log = Join-Path $installDir "launcher.log"
        $script:activeProcess = Start-HiddenPowerShell $launcher @() $log
        $script:activeMode = "launch"
        $script:activeLog = $log
        $script:lastLog = $log
        $script:launchConfirmed = $false
        Set-Busy $true
        $status.Text = "正在使用 DeepSeek 准备 Open Shift 营业日..."
    } catch {
        $script:activeProcess = $null
        Set-Busy $false
        [Windows.Forms.MessageBox]::Show($_.Exception.Message, "Open Shift", "OK", "Error") | Out-Null
    }
})

$logButton.Add_Click({
    $log = $script:lastLog
    if (-not $log) { $log = Join-Path $installDir "launcher.log" }
    if (Test-Path -LiteralPath $log) {
        Start-Process notepad.exe -ArgumentList (Quote-Argument $log)
    } else {
        [Windows.Forms.MessageBox]::Show("目前还没有安装或启动日志。", "Open Shift", "OK", "Information") | Out-Null
    }
})

$uninstallButton.Add_Click({
    $choice = [Windows.Forms.MessageBox]::Show(
        "确定删除隔离副本吗？玩家存档会保留，Steam 原版 data.win 不会改变。",
        "Open Shift",
        "YesNo",
        "Question"
    )
    if ($choice -ne "Yes") { return }
    $script = Join-Path $installDir "packaging\uninstall-open-shift.ps1"
    if (-not (Test-Path -LiteralPath $script)) { return }
    Start-Process powershell.exe -WindowStyle Hidden -ArgumentList (
        "-NoProfile -ExecutionPolicy Bypass -File " + (Quote-Argument $script) +
        " -InstallDir " + (Quote-Argument $installDir) + " -WaitForProcessId " + $PID
    )
    $form.Close()
})

$timer = New-Object Windows.Forms.Timer
$timer.Interval = 500
$timer.Add_Tick({
    if ($null -eq $script:activeProcess) { return }
    if (-not $script:activeProcess.HasExited) {
        if ($script:activeMode -eq "launch") {
            $log = Join-Path $installDir "launcher.log"
            if (Test-Path -LiteralPath $log) {
                $tail = Get-Content -LiteralPath $log -Tail 20 -ErrorAction SilentlyContinue
                if ($tail -match "day is ready") { $status.Text = "Open Shift 已准备完成，正在启动 VA-11 HALL-A..." }
                if ($tail -match "Run_Start|Entering main loop") {
                    $script:launchConfirmed = $true
                    $startButton.Enabled = $false
                    $status.Text = "游戏已启动，可以关闭此窗口。"
                }
            }
        }
        return
    }
    $script:activeProcess.WaitForExit()
    $script:activeProcess.Refresh()
    $exitCode = $script:activeProcess.ExitCode
    $mode = $script:activeMode
    $completedLog = $script:activeLog
    $completionMarker = $script:completionMarker
    $launchConfirmed = $script:launchConfirmed
    $script:activeProcess = $null
    $script:activeMode = ""
    $script:activeLog = ""
    $script:completionMarker = ""
    $script:launchConfirmed = $false
    Complete-Log $completedLog
    Set-Busy $false
    $installCompleted = $mode -eq "install" -and (Test-Path -LiteralPath $completionMarker -PathType Leaf)
    if ($exitCode -ne 0 -and -not $installCompleted -and -not $launchConfirmed) {
        $modeLabel = if ($mode -eq "install") { "安装" } else { "启动" }
        $status.Text = "$modeLabel 失败，请打开日志查看诊断信息。"
        return
    }
    if ($installCompleted) { Remove-Item -LiteralPath $completionMarker -Force -ErrorAction SilentlyContinue }
    if ($mode -eq "install") {
        try {
            if (-not [string]::IsNullOrWhiteSpace($script:pendingKey)) { Save-ApiKey $script:pendingKey }
        } catch {
            $status.Text = "已安装，但 API Key 保存失败，请重新输入。"
            [Windows.Forms.MessageBox]::Show($_.Exception.Message, "Open Shift", "OK", "Error") | Out-Null
            return
        }
        $script:pendingKey = ""
        $keyBox.Clear()
        $startButton.Enabled = $true
        $status.Text = "安装完成。Steam 原版文件未被修改。"
    } else {
        $startButton.Enabled = $true
        $status.Text = "游戏会话已结束。"
    }
})
$timer.Start()

[void] $form.ShowDialog()
