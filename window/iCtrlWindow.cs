// iCtrl's app window: the GUI page in WebView2 (the Edge engine Windows keeps
// up to date), with the browser's own features turned off so it behaves as an
// app, not a browser. Built by ictrl/window.py with the .NET Framework csc.
//
//   iCtrlWindow.exe --url http://127.0.0.1:47800/ --data <user data dir> [--icon <ico>]
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
using Microsoft.Win32;

static class Program
{
    [STAThread]
    static int Main(string[] args)
    {
        string url = Arg(args, "--url", "http://127.0.0.1:47800/");
        string data = Arg(args, "--data", Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "iCtrl", "WebView2"));
        string icon = Arg(args, "--icon", null);

        // One window: a second start brings the open one to the front.
        bool created;
        using (var mutex = new Mutex(true, "Local\\iCtrl.Window", out created))
        {
            if (!created)
            {
                Native.ShowExisting();
                return 0;
            }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new MainForm(url, data, icon));
        }
        return 0;
    }

    static string Arg(string[] args, string name, string fallback)
    {
        for (int i = 0; i + 1 < args.Length; i++)
            if (args[i] == name) return args[i + 1];
        return fallback;
    }
}

class MainForm : Form
{
    readonly string url;
    readonly string data;
    readonly WebView2 view = new WebView2();

    public MainForm(string url, string data, string icon)
    {
        this.url = url;
        this.data = data;
        Text = "iCtrl";
        float scale = Native.Scale();
        // 1180x960 at 100%, but never larger than the screen's work area.
        Rectangle work = Screen.PrimaryScreen.WorkingArea;
        Size = new Size(Math.Min((int)(1180 * scale), work.Width * 9 / 10),
                        Math.Min((int)(960 * scale), work.Height * 9 / 10));
        StartPosition = FormStartPosition.CenterScreen;
        if (icon != null && File.Exists(icon))
        {
            try { Icon = new Icon(icon); } catch (Exception) { }
        }
        // The page's own colour (style.css --bg), so the window never flashes white.
        Color bg = Native.LightTheme() ? Color.FromArgb(0xee, 0xf0, 0xf5) : Color.FromArgb(0x0a, 0x0b, 0x0f);
        BackColor = bg;
        view.DefaultBackgroundColor = bg;
        view.Dock = DockStyle.Fill;
        Controls.Add(view);
        Load += OnLoad;
        // WinForms keeps the focus on the form when the window comes to the
        // front (Alt+Tab, taskbar): hand it to the page, or keys and the
        // wheel would not reach it.
        Activated += delegate { view.Focus(); };
        SystemEvents.UserPreferenceChanged += OnThemeChanged;
    }

    protected override void OnHandleCreated(EventArgs e)
    {
        base.OnHandleCreated(e);
        Native.DarkTitleBar(Handle, !Native.LightTheme());
    }

    protected override void OnFormClosed(FormClosedEventArgs e)
    {
        SystemEvents.UserPreferenceChanged -= OnThemeChanged;
        base.OnFormClosed(e);
    }

    void OnThemeChanged(object sender, UserPreferenceChangedEventArgs e)
    {
        if (e.Category != UserPreferenceCategory.General) return;
        bool light = Native.LightTheme();
        Native.DarkTitleBar(Handle, !light);
        if (view.CoreWebView2 != null)
            view.CoreWebView2.Profile.PreferredColorScheme =
                light ? CoreWebView2PreferredColorScheme.Light : CoreWebView2PreferredColorScheme.Dark;
    }

    async void OnLoad(object sender, EventArgs e)
    {
        try
        {
            Directory.CreateDirectory(data);
            var env = await CoreWebView2Environment.CreateAsync(null, data, new CoreWebView2EnvironmentOptions());
            await view.EnsureCoreWebView2Async(env);
        }
        catch (Exception ex)
        {
            // No WebView2 runtime (it ships with Windows 11 and current Windows 10).
            MessageBox.Show(this, "iCtrl の画面を開けませんでした（WebView2: " + ex.Message + "）。\n既定のブラウザーで開きます。",
                "iCtrl", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            OpenExternal(url);
            Close();
            return;
        }
        CoreWebView2 core = view.CoreWebView2;
        CoreWebView2Settings s = core.Settings;
        // Browser features iCtrl does not use: they would make the window act
        // like Edge (F5/Ctrl+F/Ctrl+P, zoom, autofill, status bar, DevTools).
        s.AreBrowserAcceleratorKeysEnabled = false;
        s.AreDevToolsEnabled = false;
        s.IsStatusBarEnabled = false;
        s.IsZoomControlEnabled = false;
        s.IsPinchZoomEnabled = false;
        s.IsSwipeNavigationEnabled = false;
        s.IsGeneralAutofillEnabled = false;
        s.IsPasswordAutosaveEnabled = false;
        s.IsBuiltInErrorPageEnabled = true;
        core.Profile.PreferredColorScheme =
            Native.LightTheme() ? CoreWebView2PreferredColorScheme.Light : CoreWebView2PreferredColorScheme.Dark;
        core.DocumentTitleChanged += delegate { Text = string.IsNullOrEmpty(core.DocumentTitle) ? "iCtrl" : core.DocumentTitle; };
        core.ContextMenuRequested += OnContextMenu;
        core.NewWindowRequested += delegate (object o, CoreWebView2NewWindowRequestedEventArgs a)
        {
            a.Handled = true;
            OpenExternal(a.Uri);
        };
        core.NavigationStarting += delegate (object o, CoreWebView2NavigationStartingEventArgs a)
        {
            // Only iCtrl's own pages open here; anything else goes to the browser.
            if (!a.Uri.StartsWith(url, StringComparison.OrdinalIgnoreCase))
            {
                a.Cancel = true;
                OpenExternal(a.Uri);
            }
        };
        core.Navigate(url);
    }

    // Keep only the text editing items (the phone view has its own menu).
    static void OnContextMenu(object sender, CoreWebView2ContextMenuRequestedEventArgs e)
    {
        var items = e.MenuItems;
        for (int i = items.Count - 1; i >= 0; i--)
        {
            string name = items[i].Name;
            if (name != "copy" && name != "cut" && name != "paste" && name != "selectAll")
                items.RemoveAt(i);
        }
        // Drop separators left at the edges or doubled.
        for (int i = items.Count - 1; i >= 0; i--)
        {
            bool sep = items[i].Kind == CoreWebView2ContextMenuItemKind.Separator;
            if (sep && (i == 0 || i == items.Count - 1 || items[i - 1].Kind == CoreWebView2ContextMenuItemKind.Separator))
                items.RemoveAt(i);
        }
        if (items.Count == 0) e.Handled = true;
    }

    static void OpenExternal(string uri)
    {
        if (uri == null || !(uri.StartsWith("http://") || uri.StartsWith("https://"))) return;
        try { Process.Start(new ProcessStartInfo(uri) { UseShellExecute = true }); } catch (Exception) { }
    }
}

static class Native
{
    [DllImport("dwmapi.dll")]
    static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int value, int size);
    [DllImport("user32.dll")]
    static extern bool SetForegroundWindow(IntPtr hwnd);
    [DllImport("user32.dll")]
    static extern bool ShowWindow(IntPtr hwnd, int cmd);
    [DllImport("user32.dll")]
    static extern bool IsIconic(IntPtr hwnd);
    [DllImport("user32.dll")]
    static extern uint GetDpiForSystem();

    public static float Scale()
    {
        try { return GetDpiForSystem() / 96f; } catch (Exception) { return 1f; }
    }

    public static bool LightTheme()
    {
        object v = Registry.GetValue(
            @"HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize", "AppsUseLightTheme", 1);
        return !(v is int) || (int)v != 0;
    }

    public static void DarkTitleBar(IntPtr hwnd, bool dark)
    {
        int on = dark ? 1 : 0;
        DwmSetWindowAttribute(hwnd, 20, ref on, 4);
    }

    public static void ShowExisting()
    {
        Process me = Process.GetCurrentProcess();
        foreach (Process p in Process.GetProcessesByName(me.ProcessName))
        {
            if (p.Id == me.Id || p.MainWindowHandle == IntPtr.Zero) continue;
            if (IsIconic(p.MainWindowHandle)) ShowWindow(p.MainWindowHandle, 9); // SW_RESTORE
            SetForegroundWindow(p.MainWindowHandle);
            return;
        }
    }
}
