using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Win32;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

internal sealed class OpenShiftLauncherForm : Form
{
    private readonly string root;
    private readonly string installDir;
    private readonly string gameCopyDir;
    private readonly string runtimeConfigPath;
    private readonly WebView2 browser;
    private Process activeProcess;
    private string activeMode = "";
    private string activeLog = "";
    private string pendingKey = "";
    private string selectedSteamGame = "";
    private string completionMarker = "";
    private bool launchConfirmed;
    private StreamWriter activeLogWriter;

    public OpenShiftLauncherForm()
    {
        root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
        installDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "OpenShift");
        gameCopyDir = Path.Combine(installDir, "game");
        runtimeConfigPath = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "VA_11_Hall_A", "open-shift.toml");
        selectedSteamGame = FindSteamGame();
        Text = "OPEN SHIFT";
        ClientSize = new Size(540, 790);
        MinimumSize = ClientSize;
        MaximumSize = ClientSize;
        StartPosition = FormStartPosition.CenterScreen;
        FormBorderStyle = FormBorderStyle.FixedSingle;
        MaximizeBox = false;
        Icon = LoadIcon();
        browser = new WebView2 { Dock = DockStyle.Fill, CreationProperties = new CoreWebView2CreationProperties() };
        Controls.Add(browser);
        Load += async (sender, args) => await InitializeBrowser();
        FormClosed += (sender, args) => { if (activeProcess != null && !activeProcess.HasExited) activeProcess.Dispose(); };
    }

    private Icon LoadIcon()
    {
        string path = Path.Combine(root, "OpenShift.ico");
        return File.Exists(path) ? new Icon(path) : SystemIcons.Application;
    }

    private async Task InitializeBrowser()
    {
        try
        {
            await browser.EnsureCoreWebView2Async(null);
            browser.CoreWebView2.Settings.AreDevToolsEnabled = false;
            browser.CoreWebView2.Settings.AreDefaultContextMenusEnabled = false;
            browser.CoreWebView2.WebMessageReceived += OnWebMessage;
            browser.CoreWebView2.NavigationCompleted += (sender, args) => SendState("就绪。Steam 原版文件保持只读。", false);
            string page = Path.Combine(root, "packaging", "webview", "index.html");
            if (!File.Exists(page)) throw new FileNotFoundException("WebView2 interface is missing.", page);
            browser.CoreWebView2.Navigate(new Uri(page).AbsoluteUri);
        }
        catch (Exception error)
        {
            string message = error is WebView2RuntimeNotFoundException
                ? "没有检测到 Microsoft Edge WebView2 Runtime。请先安装 WebView2 Evergreen Runtime，再重新打开 OPEN SHIFT。\r\n\r\n" + error.Message
                : "OPEN SHIFT 图形界面无法启动。请确认发行包已完整解压。\r\n\r\n" + error.Message;
            MessageBox.Show(message, "OPEN SHIFT", MessageBoxButtons.OK, MessageBoxIcon.Error);
            Close();
        }
    }

    private void SendState(string message, bool busy)
    {
        if (browser.CoreWebView2 == null) return;
        bool thinkingAvailable;
        string thinking = ReadThinkingMode(out thinkingAvailable);
        string json = "{\"steam\":" + Json(selectedSteamGame) + ",\"copy\":" + Json(gameCopyDir) + ",\"status\":" + Json(message) + ",\"busy\":" + (busy ? "true" : "false") + ",\"startDisabled\":" + (!File.Exists(Path.Combine(installDir, "Start-Open-Shift.ps1")) ? "true" : "false") + ",\"thinking\":" + Json(thinking) + ",\"thinkingAvailable\":" + (thinkingAvailable ? "true" : "false") + "}";
        browser.CoreWebView2.ExecuteScriptAsync("window.setState(" + json + ");");
    }

    private static string Json(string value)
    {
        string text = value ?? "";
        return "\"" + text.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\r", "\\r").Replace("\n", "\\n") + "\"";
    }

    private string FindSteamGame()
    {
        string installed = ReadInstalledSteamGame();
        if (IsGameDirectory(installed)) return installed;

        foreach (string rootPath in FindSteamRoots())
        {
            string candidate = Path.Combine(rootPath, "steamapps", "common", "VA-11 HALL-A");
            if (IsGameDirectory(candidate)) return Path.GetFullPath(candidate);
        }
        return "";
    }

    private string ReadInstalledSteamGame()
    {
        string statePath = Path.Combine(installDir, "install.json");
        if (!File.Exists(statePath)) return "";
        try
        {
            using (JsonDocument document = JsonDocument.Parse(File.ReadAllText(statePath, Encoding.UTF8)))
            {
                JsonElement value;
                return document.RootElement.TryGetProperty("steam_game_dir", out value) ? value.GetString() ?? "" : "";
            }
        }
        catch { return ""; }
    }

    private static IEnumerable<string> FindSteamRoots()
    {
        var roots = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        AddSteamRoot(roots, Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86), "Steam"));
        AddSteamRoot(roots, Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "Steam"));
        AddSteamRoot(roots, "C:\\Steam");
        AddSteamRoot(roots, ReadSteamRegistryPath(Registry.CurrentUser, @"Software\Valve\Steam", "SteamPath"));
        AddSteamRoot(roots, ReadSteamRegistryPath(Registry.LocalMachine, @"Software\WOW6432Node\Valve\Steam", "InstallPath"));

        foreach (string steamRoot in new List<string>(roots))
        {
            string libraries = Path.Combine(steamRoot, "steamapps", "libraryfolders.vdf");
            if (!File.Exists(libraries)) continue;
            try
            {
                string content = File.ReadAllText(libraries);
                foreach (Match match in Regex.Matches(content, "\\\"path\\\"\\s+\\\"(?<path>[^\\\"]+)\\\"", RegexOptions.IgnoreCase))
                    AddSteamRoot(roots, match.Groups["path"].Value.Replace("\\\\", "\\"));
                foreach (Match match in Regex.Matches(content, "\\\"\\d+\\\"\\s+\\\"(?<path>[^\\\"]+)\\\""))
                    AddSteamRoot(roots, match.Groups["path"].Value.Replace("\\\\", "\\"));
            }
            catch { }
        }
        return roots;
    }

    private static void AddSteamRoot(HashSet<string> roots, string value)
    {
        if (String.IsNullOrWhiteSpace(value)) return;
        try { roots.Add(Path.GetFullPath(value.Trim().TrimEnd(Path.DirectorySeparatorChar))); } catch { }
    }

    private static string ReadSteamRegistryPath(RegistryKey hive, string keyName, string valueName)
    {
        try
        {
            using (RegistryKey key = hive.OpenSubKey(keyName))
                return key == null ? "" : Convert.ToString(key.GetValue(valueName)) ?? "";
        }
        catch { return ""; }
    }

    private static bool IsGameDirectory(string path)
    {
        return !String.IsNullOrWhiteSpace(path) && File.Exists(Path.Combine(path, "data.win"));
    }

    private void OnWebMessage(object sender, CoreWebView2WebMessageReceivedEventArgs args)
    {
        try
        {
            string message = args.TryGetWebMessageAsString();
            string action = Match(message, "action");
            if (activeProcess != null && !activeProcess.HasExited && action != "logs")
            {
                SendState("当前操作仍在进行，请稍候。", true);
                return;
            }
            if (action == "browse") Browse();
            else if (action == "saveKey") SaveApiKey(Match(message, "value"));
            else if (action == "install") Install(Match(message, "steam"), Match(message, "key"));
            else if (action == "start") StartGame();
            else if (action == "thinking") SetThinkingMode(Match(message, "value"));
            else if (action == "logs") OpenLogs();
            else if (action == "uninstall") Uninstall();
            else if (action == "steamChanged") selectedSteamGame = Match(message, "value").Trim();
        }
        catch (Exception error) { SendState(error.Message, false); }
    }

    private static string Match(string json, string key)
    {
        Match match = Regex.Match(json ?? "", "\\\"" + Regex.Escape(key) + "\\\"\\s*:\\s*\\\"(?<value>(?:\\\\.|[^\\\"])*)\\\"");
        return match.Success ? Regex.Unescape(match.Groups["value"].Value) : "";
    }

    private void Browse()
    {
        using (var dialog = new FolderBrowserDialog { Description = "选择包含 data.win 的 VA-11 HALL-A Steam 游戏目录" })
            if (dialog.ShowDialog(this) == DialogResult.OK)
            {
                selectedSteamGame = dialog.SelectedPath;
                browser.CoreWebView2.ExecuteScriptAsync("window.setState({steam:" + Json(selectedSteamGame) + "});");
            }
    }

    private void SaveApiKey(string value)
    {
        if (String.IsNullOrWhiteSpace(value)) throw new InvalidOperationException("DeepSeek API Key 不能为空。");
        Directory.CreateDirectory(installDir);
        byte[] bytes = Encoding.UTF8.GetBytes(value.Trim());
        try { File.WriteAllBytes(Path.Combine(installDir, "api-key.dpapi"), ProtectedData.Protect(bytes, null, DataProtectionScope.CurrentUser)); }
        finally { Array.Clear(bytes, 0, bytes.Length); }
        SendState("DeepSeek API Key 已为当前 Windows 用户加密保存。", false);
    }

    private string ReadThinkingMode(out bool available)
    {
        available = false;
        if (!File.Exists(runtimeConfigPath)) return "disabled";
        try
        {
            bool inProvider = false;
            string found = "";
            int matches = 0;
            foreach (string line in File.ReadAllLines(runtimeConfigPath, Encoding.UTF8))
            {
                string trimmed = line.Trim();
                if (trimmed.StartsWith("[") && trimmed.EndsWith("]"))
                {
                    inProvider = String.Equals(trimmed, "[provider]", StringComparison.OrdinalIgnoreCase);
                    continue;
                }
                if (!inProvider) continue;
                Match match = Regex.Match(line, "^\\s*thinking\\s*=\\s*\\\"(?<mode>default|enabled|balanced|disabled)\\\"\\s*(?:#.*)?$", RegexOptions.IgnoreCase);
                if (!match.Success) continue;
                found = match.Groups["mode"].Value.ToLowerInvariant();
                matches++;
            }
            available = matches == 1;
            return available ? (found == "default" ? "disabled" : found) : "disabled";
        }
        catch { return "disabled"; }
    }

    private void SetThinkingMode(string value)
    {
        string mode = (value ?? "").Trim().ToLowerInvariant();
        if (mode != "enabled" && mode != "balanced" && mode != "disabled")
            throw new InvalidOperationException("DeepSeek Thinking 模式无效。");
        if (!File.Exists(runtimeConfigPath))
            throw new InvalidOperationException("请先安装 OPEN SHIFT，再切换 DeepSeek Thinking。");

        string[] lines = File.ReadAllLines(runtimeConfigPath, Encoding.UTF8);
        bool inProvider = false;
        int thinkingLine = -1;
        for (int index = 0; index < lines.Length; index++)
        {
            string trimmed = lines[index].Trim();
            if (trimmed.StartsWith("[") && trimmed.EndsWith("]"))
            {
                inProvider = String.Equals(trimmed, "[provider]", StringComparison.OrdinalIgnoreCase);
                continue;
            }
            if (!inProvider || !Regex.IsMatch(lines[index], "^\\s*thinking\\s*=")) continue;
            if (thinkingLine >= 0)
                throw new InvalidOperationException("运行配置中存在重复的 thinking 设置。");
            if (!Regex.IsMatch(lines[index], "^\\s*thinking\\s*=\\s*\\\"(?:default|enabled|balanced|disabled)\\\"\\s*(?:#.*)?$", RegexOptions.IgnoreCase))
                throw new InvalidOperationException("运行配置中的 thinking 设置无法安全修改。");
            thinkingLine = index;
        }
        if (thinkingLine < 0)
            throw new InvalidOperationException("运行配置中缺少 provider.thinking 设置，请执行安装 / 修复。");

        string indentation = Regex.Match(lines[thinkingLine], "^\\s*").Value;
        lines[thinkingLine] = indentation + "thinking = \"" + mode + "\"";
        string temporary = runtimeConfigPath + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            File.WriteAllLines(temporary, lines, new UTF8Encoding(false));
            ValidateRuntimeConfig(temporary);
            File.Replace(temporary, runtimeConfigPath, null);
        }
        finally
        {
            if (File.Exists(temporary)) try { File.Delete(temporary); } catch { }
        }
        string label = mode == "enabled" ? "深度" : mode == "balanced" ? "平衡" : "快速";
        SendState("DeepSeek 模式已切换为“" + label + "”，下次生成时生效。", false);
    }

    private void ValidateRuntimeConfig(string configPath)
    {
        string statePath = Path.Combine(installDir, "install.json");
        if (!File.Exists(statePath)) throw new InvalidOperationException("安装状态不存在，请执行安装 / 修复。");
        string runtime;
        bool runtimeIsPython;
        using (JsonDocument document = JsonDocument.Parse(File.ReadAllText(statePath, Encoding.UTF8)))
        {
            JsonElement runtimeValue;
            JsonElement pythonValue;
            if (!document.RootElement.TryGetProperty("runtime", out runtimeValue) || String.IsNullOrWhiteSpace(runtimeValue.GetString()))
                throw new InvalidOperationException("安装状态中没有可用的 OPEN SHIFT 运行时。");
            runtime = runtimeValue.GetString();
            runtimeIsPython = document.RootElement.TryGetProperty("runtime_is_python", out pythonValue) && pythonValue.GetBoolean();
        }
        if (!File.Exists(runtime)) throw new InvalidOperationException("OPEN SHIFT 运行时不存在，请执行安装 / 修复。");

        string arguments = runtimeIsPython
            ? "-m open_shift validate-config --config " + Quote(configPath)
            : "validate-config --config " + Quote(configPath);
        var info = new ProcessStartInfo(runtime, arguments)
        {
            WorkingDirectory = installDir,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        if (runtimeIsPython) info.EnvironmentVariables["PYTHONPATH"] = Path.Combine(installDir, "src");
        using (Process process = Process.Start(info) ?? throw new InvalidOperationException("无法启动配置校验。"))
        {
            string output = process.StandardOutput.ReadToEnd();
            string error = process.StandardError.ReadToEnd();
            if (!process.WaitForExit(30000))
            {
                try { process.Kill(); } catch { }
                throw new InvalidOperationException("运行配置校验超时。");
            }
            if (process.ExitCode != 0)
                throw new InvalidOperationException("运行配置校验失败：" + (String.IsNullOrWhiteSpace(error) ? output.Trim() : error.Trim()));
        }
    }

    private void Install(string steam, string key)
    {
        selectedSteamGame = (steam ?? "").Trim();
        if (!IsGameDirectory(selectedSteamGame)) throw new InvalidOperationException("请选择包含 data.win 的 VA-11 HALL-A Steam 游戏目录。");
        pendingKey = key;
        string log = Path.Combine(Path.GetTempPath(), "open-shift-install.log");
        completionMarker = Path.Combine(Path.GetTempPath(), "open-shift-install-" + Guid.NewGuid().ToString("N") + ".complete");
        StartPowerShell(Path.Combine(root, "packaging", "install-open-shift.ps1"), "-SteamGameDir " + Quote(selectedSteamGame) + " -InstallDir " + Quote(installDir) + " -GameCopyDir " + Quote(gameCopyDir) + " -CompletionMarker " + Quote(completionMarker) + " -SkipCredential -SkipShortcut", log);
        SendState("正在校验 Steam 文件并生成隔离副本...", true);
    }

    private void StartGame()
    {
        string launcher = Path.Combine(installDir, "Start-Open-Shift.ps1");
        if (!File.Exists(launcher)) throw new InvalidOperationException("请先安装 Open Shift。");
        StartPowerShell(launcher, "", Path.Combine(installDir, "launcher.log"));
        SendState("正在使用 DeepSeek 准备 Open Shift 营业日...", true);
    }

    private void StartPowerShell(string script, string arguments, string log)
    {
        if (activeProcess != null && !activeProcess.HasExited) throw new InvalidOperationException("当前操作仍在进行，请稍候。");
        activeMode = script.EndsWith("install-open-shift.ps1", StringComparison.OrdinalIgnoreCase) ? "install" : "launch";
        activeLog = log;
        launchConfirmed = false;
        string logDirectory = Path.GetDirectoryName(log);
        if (!String.IsNullOrEmpty(logDirectory)) Directory.CreateDirectory(logDirectory);
        activeLogWriter = new StreamWriter(log, false, new UTF8Encoding(false)) { AutoFlush = true };
        var info = new ProcessStartInfo("powershell.exe", "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File " + Quote(script) + " " + arguments)
        { WorkingDirectory = root, UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true };
        activeProcess = Process.Start(info) ?? throw new InvalidOperationException("无法启动 PowerShell 子进程。");
        activeProcess.EnableRaisingEvents = true;
        StreamWriter writer = activeLogWriter;
        activeProcess.OutputDataReceived += (sender, args) => HandleProcessOutput(writer, args.Data);
        activeProcess.ErrorDataReceived += (sender, args) => HandleProcessOutput(writer, args.Data);
        activeProcess.BeginOutputReadLine();
        activeProcess.BeginErrorReadLine();
        activeProcess.Exited += (sender, args) => BeginInvoke((Action)ProcessFinished);
    }

    private void HandleProcessOutput(StreamWriter writer, string line)
    {
        if (line == null) return;
        lock (writer) writer.WriteLine(line);
        if (activeMode != "launch") return;
        if (line.IndexOf("day is ready", StringComparison.OrdinalIgnoreCase) >= 0)
            BeginInvoke((Action)(() => SendState("OPEN SHIFT 已准备完成，正在启动 VA-11 HALL-A...", true)));
        if (line.IndexOf("Run_Start", StringComparison.OrdinalIgnoreCase) >= 0 || line.IndexOf("Entering main loop", StringComparison.OrdinalIgnoreCase) >= 0)
        {
            launchConfirmed = true;
            BeginInvoke((Action)(() => SendState("游戏已启动，可以关闭此窗口。", true)));
        }
    }

    private void ProcessFinished()
    {
        activeProcess.WaitForExit();
        int code = activeProcess.ExitCode;
        bool markerExists = activeMode == "install" && !String.IsNullOrWhiteSpace(completionMarker) && File.Exists(completionMarker);
        bool installSucceeded = activeMode == "install" && (code == 0 || markerExists) && File.Exists(Path.Combine(installDir, "Start-Open-Shift.ps1"));
        bool launchSucceeded = activeMode == "launch" && (code == 0 || launchConfirmed);
        string result;
        if (installSucceeded)
        {
            if (!String.IsNullOrWhiteSpace(pendingKey))
            {
                try { SaveApiKey(pendingKey); }
                catch (Exception error) { result = "安装完成，但 API Key 保存失败：" + error.Message; goto Finished; }
            }
            result = "安装完成。Steam 原版文件未被修改。";
        }
        else if (launchSucceeded) result = "游戏会话已结束。";
        else result = (activeMode == "install" ? "安装" : "启动") + "失败，请打开日志查看诊断信息。";
Finished:
        pendingKey = "";
        if (markerExists) try { File.Delete(completionMarker); } catch { }
        completionMarker = "";
        if (activeLogWriter != null) { activeLogWriter.Dispose(); activeLogWriter = null; }
        activeProcess.Dispose(); activeProcess = null;
        activeMode = "";
        SendState(result, false);
    }

    private void OpenLogs()
    {
        string log = String.IsNullOrWhiteSpace(activeLog) ? Path.Combine(installDir, "launcher.log") : activeLog;
        if (File.Exists(log)) Process.Start("notepad.exe", Quote(log)); else SendState("目前还没有安装或启动日志。", false);
    }

    private void Uninstall()
    {
        string script = Path.Combine(installDir, "packaging", "uninstall-open-shift.ps1");
        if (!File.Exists(script)) throw new InvalidOperationException("没有找到卸载脚本。");
        DialogResult choice = MessageBox.Show(
            "确定删除 OPEN SHIFT 的隔离副本吗？\r\n\r\n玩家存档会保留，Steam 原版 data.win 不会改变。",
            "OPEN SHIFT",
            MessageBoxButtons.YesNo,
            MessageBoxIcon.Question,
            MessageBoxDefaultButton.Button2);
        if (choice != DialogResult.Yes) return;
        Process.Start(new ProcessStartInfo("powershell.exe", "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File " + Quote(script) + " -InstallDir " + Quote(installDir) + " -WaitForProcessId " + Process.GetCurrentProcess().Id) { UseShellExecute = false, CreateNoWindow = true });
        Close();
    }

    private static string Quote(string value) { return "\"" + (value ?? "").Replace("\"", "\\\"") + "\""; }
}

internal static class OpenShiftGuiLauncher
{
    [STAThread]
    private static void Main()
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new OpenShiftLauncherForm());
    }
}
