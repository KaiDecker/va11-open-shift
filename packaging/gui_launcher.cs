using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

internal sealed class OpenShiftLauncherForm : Form
{
    private readonly string root;
    private readonly string installDir;
    private readonly string gameCopyDir;
    private readonly WebView2 browser;
    private Process activeProcess;
    private string activeMode = "";
    private string activeLog = "";
    private string pendingKey = "";
    private StreamWriter activeLogWriter;

    public OpenShiftLauncherForm()
    {
        root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
        installDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "OpenShift");
        gameCopyDir = Path.Combine(installDir, "game");
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
            MessageBox.Show(error.Message, "OPEN SHIFT", MessageBoxButtons.OK, MessageBoxIcon.Error);
            Close();
        }
    }

    private void SendState(string message, bool busy)
    {
        if (browser.CoreWebView2 == null) return;
        string json = "{\"steam\":" + Json(FindSteamGame()) + ",\"copy\":" + Json(gameCopyDir) + ",\"status\":" + Json(message) + ",\"busy\":" + (busy ? "true" : "false") + ",\"startDisabled\":" + (!File.Exists(Path.Combine(installDir, "Start-Open-Shift.ps1")) ? "true" : "false") + "}";
        browser.CoreWebView2.ExecuteScriptAsync("window.setState(" + json + ");");
    }

    private static string Json(string value)
    {
        string text = value ?? "";
        return "\"" + text.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\r", "\\r").Replace("\n", "\\n") + "\"";
    }

    private string FindSteamGame()
    {
        string[] roots = { Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86), Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "C:\\Steam" };
        foreach (string rootPath in roots)
        {
            if (String.IsNullOrEmpty(rootPath)) continue;
            string candidate = Path.Combine(rootPath, "Steam", "steamapps", "common", "VA-11 HALL-A");
            if (File.Exists(Path.Combine(candidate, "data.win"))) return candidate;
        }
        return "";
    }

    private void OnWebMessage(object sender, CoreWebView2WebMessageReceivedEventArgs args)
    {
        try
        {
            string message = args.TryGetWebMessageAsString();
            string action = Match(message, "action");
            if (action == "browse") Browse();
            else if (action == "saveKey") SaveApiKey(Match(message, "value"));
            else if (action == "install") Install(Match(message, "steam"), Match(message, "key"));
            else if (action == "start") StartGame();
            else if (action == "logs") OpenLogs();
            else if (action == "uninstall") Uninstall();
            else if (action == "close") Close();
            else if (action == "minimize") WindowState = FormWindowState.Minimized;
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
            if (dialog.ShowDialog(this) == DialogResult.OK) browser.CoreWebView2.ExecuteScriptAsync("window.setState({steam:" + Json(dialog.SelectedPath) + "});");
    }

    private void SaveApiKey(string value)
    {
        if (String.IsNullOrWhiteSpace(value)) throw new InvalidOperationException("API key cannot be empty.");
        Directory.CreateDirectory(installDir);
        byte[] bytes = Encoding.UTF8.GetBytes(value.Trim());
        try { File.WriteAllBytes(Path.Combine(installDir, "api-key.dpapi"), ProtectedData.Protect(bytes, null, DataProtectionScope.CurrentUser)); }
        finally { Array.Clear(bytes, 0, bytes.Length); }
        SendState("DeepSeek API Key 已为当前 Windows 用户加密保存。", false);
    }

    private void Install(string steam, string key)
    {
        if (!File.Exists(Path.Combine(steam, "data.win"))) throw new InvalidOperationException("请选择包含 data.win 的 VA-11 HALL-A Steam 游戏目录。");
        pendingKey = key;
        string log = Path.Combine(Path.GetTempPath(), "open-shift-install.log");
        string marker = Path.Combine(Path.GetTempPath(), "open-shift-install-" + Guid.NewGuid().ToString("N") + ".complete");
        StartPowerShell(Path.Combine(root, "packaging", "install-open-shift.ps1"), "-SteamGameDir " + Quote(steam) + " -InstallDir " + Quote(installDir) + " -GameCopyDir " + Quote(gameCopyDir) + " -CompletionMarker " + Quote(marker) + " -SkipCredential", log);
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
        activeMode = script.EndsWith("install-open-shift.ps1", StringComparison.OrdinalIgnoreCase) ? "install" : "launch";
        activeLog = log;
        if (activeProcess != null && !activeProcess.HasExited) return;
        string logDirectory = Path.GetDirectoryName(log);
        if (!String.IsNullOrEmpty(logDirectory)) Directory.CreateDirectory(logDirectory);
        activeLogWriter = new StreamWriter(log, false, new UTF8Encoding(false)) { AutoFlush = true };
        var info = new ProcessStartInfo("powershell.exe", "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File " + Quote(script) + " " + arguments)
        { WorkingDirectory = root, UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true };
        activeProcess = Process.Start(info);
        activeProcess.EnableRaisingEvents = true;
        StreamWriter writer = activeLogWriter;
        activeProcess.OutputDataReceived += (sender, args) => { if (args.Data != null) lock (writer) writer.WriteLine(args.Data); };
        activeProcess.ErrorDataReceived += (sender, args) => { if (args.Data != null) lock (writer) writer.WriteLine(args.Data); };
        activeProcess.BeginOutputReadLine();
        activeProcess.BeginErrorReadLine();
        activeProcess.Exited += (sender, args) => BeginInvoke((Action)ProcessFinished);
    }

    private void ProcessFinished()
    {
        activeProcess.WaitForExit();
        int code = activeProcess.ExitCode;
        if (activeMode == "install" && !String.IsNullOrWhiteSpace(pendingKey)) { try { SaveApiKey(pendingKey); } catch { } pendingKey = ""; }
        SendState(code == 0 ? (activeMode == "install" ? "安装完成。Steam 原版文件未被修改。" : "游戏会话已启动。") : "操作失败，请打开日志查看诊断信息。", false);
        if (activeLogWriter != null) { activeLogWriter.Dispose(); activeLogWriter = null; }
        activeProcess.Dispose(); activeProcess = null;
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
