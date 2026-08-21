using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class OpenShiftGuiLauncher
{
    [STAThread]
    private static void Main()
    {
        try
        {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            string script = Path.Combine(root, "packaging", "open-shift-gui.ps1");
            if (!File.Exists(script))
                throw new FileNotFoundException("The Open Shift GUI script is missing.", script);

            var start = new ProcessStartInfo
            {
                FileName = "powershell.exe",
                Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"" + script + "\"",
                WorkingDirectory = root,
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden,
            };
            Process.Start(start);
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "OPEN SHIFT 安装器", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
