#define WIN32_LEAN_AND_MEAN
#define NOMINMAX

#include <windows.h>
#include <commdlg.h>
#include <shlobj.h>
#include <shellapi.h>
#include <wincrypt.h>
#include <wrl.h>
#include <WebView2.h>

#include <algorithm>
#include <cctype>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <string>
#include <vector>

#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "user32.lib")
#pragma comment(lib, "advapi32.lib")
#pragma comment(lib, "comdlg32.lib")
#pragma comment(lib, "shell32.lib")
#pragma comment(lib, "crypt32.lib")

using Microsoft::WRL::Callback;
using Microsoft::WRL::ComPtr;

namespace {

std::wstring ModuleRoot() {
    wchar_t buffer[MAX_PATH]{};
    DWORD size = GetModuleFileNameW(nullptr, buffer, MAX_PATH);
    std::wstring path(buffer, size);
    const size_t slash = path.find_last_of(L"\\/");
    return slash == std::wstring::npos ? L"." : path.substr(0, slash);
}

std::wstring ReadUtf8(const std::wstring& path) {
    std::ifstream input(path, std::ios::binary);
    std::string bytes((std::istreambuf_iterator<char>(input)), {});
    if (bytes.empty()) return {};
    int count = MultiByteToWideChar(CP_UTF8, 0, bytes.data(), static_cast<int>(bytes.size()), nullptr, 0);
    std::wstring result(count, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, bytes.data(), static_cast<int>(bytes.size()), result.data(), count);
    return result;
}

void WriteUtf8(const std::wstring& path, const std::wstring& value) {
    int count = WideCharToMultiByte(CP_UTF8, 0, value.data(), static_cast<int>(value.size()), nullptr, 0, nullptr, nullptr);
    std::string bytes(count, '\0');
    WideCharToMultiByte(CP_UTF8, 0, value.data(), static_cast<int>(value.size()), bytes.data(), count, nullptr, nullptr);
    std::ofstream output(path, std::ios::binary | std::ios::trunc);
    output.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
}

bool FileExists(const std::wstring& path) { return GetFileAttributesW(path.c_str()) != INVALID_FILE_ATTRIBUTES; }
std::wstring Join(const std::wstring& left, const std::wstring& right) { return left + L"\\" + right; }

std::wstring JsonString(const std::wstring& value) {
    std::wostringstream out;
    out << L'"';
    for (wchar_t c : value) {
        switch (c) {
        case L'\\': out << L"\\\\"; break;
        case L'"': out << L"\\\""; break;
        case L'\r': out << L"\\r"; break;
        case L'\n': out << L"\\n"; break;
        case L'\t': out << L"\\t"; break;
        default:
            if (c < 0x20 || c > 0x7e) out << L"\\u" << std::hex << std::uppercase << std::setw(4) << std::setfill(L'0') << static_cast<int>(c) << std::setfill(L' ') << std::dec;
            else out << c;
        }
    }
    out << L'"';
    return out.str();
}

std::wstring JsonValue(const std::wstring& json, const std::wstring& key) {
    const std::wstring needle = L"\"" + key + L"\"";
    size_t start = json.find(needle);
    if (start == std::wstring::npos) return {};
    start = json.find(L':', start + needle.size());
    if (start == std::wstring::npos) return {};
    start = json.find(L'"', start + 1);
    if (start == std::wstring::npos) return {};
    std::wstring result;
    bool escaped = false;
    for (size_t i = start + 1; i < json.size(); ++i) {
        wchar_t c = json[i];
        if (!escaped && c == L'"') break;
        if (escaped) {
            if (c == L'n') result += L'\n';
            else if (c == L'r') result += L'\r';
            else if (c == L't') result += L'\t';
            else if (c == L'u' && i + 4 < json.size()) {
                unsigned int code = 0;
                for (int j = 1; j <= 4; ++j) code = code * 16 + (json[i + j] >= L'a' ? json[i + j] - L'a' + 10 : json[i + j] >= L'A' ? json[i + j] - L'A' + 10 : json[i + j] - L'0');
                result += static_cast<wchar_t>(code);
                i += 4;
            } else result += c;
            escaped = false;
        } else if (c == L'\\') escaped = true;
        else result += c;
    }
    return result;
}

std::wstring SafeVersion(std::wstring value) {
    for (wchar_t& c : value) if (!((c >= L'a' && c <= L'z') || (c >= L'A' && c <= L'Z') || (c >= L'0' && c <= L'9') || c == L'.' || c == L'-' || c == L'_')) c = L'-';
    while (!value.empty() && (value.back() == L'.' || value.back() == L'-')) value.pop_back();
    return value.empty() ? L"development" : value;
}

bool IsGameDir(const std::wstring& path) { return !path.empty() && FileExists(Join(path, L"data.win")); }

class Launcher {
public:
    HWND window{};
    ComPtr<ICoreWebView2Controller> controller;
    ComPtr<ICoreWebView2> webview;
    std::wstring root = ModuleRoot();
    std::wstring packageVersion = L"development";
    std::wstring installDir;
    std::wstring gameCopy;
    std::wstring selectedGame;
    std::wstring pendingKey;
    std::wstring activeLog;
    std::wstring marker;
    HANDLE process = nullptr;
    HANDLE output = nullptr;
    bool installing = false;
    bool launchConfirmed = false;

    Launcher() {
        std::wstring manifest = ReadUtf8(Join(root, L"PACKAGE_MANIFEST.json"));
        std::wstring version = JsonValue(manifest, L"package_version");
        if (version.empty()) version = L"development";
        packageVersion = version;
        std::wstring statePath = Join(root, L"install.json");
        std::wstring state = ReadUtf8(statePath);
        bool same = !state.empty() && JsonValue(state, L"package_version") == version;
        wchar_t local[MAX_PATH]{};
        GetEnvironmentVariableW(L"LOCALAPPDATA", local, MAX_PATH);
        installDir = same ? root : Join(local, L"OpenShift-" + SafeVersion(version));
        gameCopy = Join(installDir, L"game");
        selectedGame = FindGame();
    }

    std::wstring FindGame() {
        std::vector<std::wstring> roots = { L"C:\\Program Files (x86)\\Steam", L"C:\\Program Files\\Steam", L"C:\\Steam" };
        for (auto hive : { HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE }) {
            HKEY key{}; wchar_t value[MAX_PATH]{}; DWORD size = sizeof(value); DWORD type = 0;
            if (RegOpenKeyExW(hive, L"Software\\Valve\\Steam", 0, KEY_READ, &key) == ERROR_SUCCESS) {
                if (RegQueryValueExW(key, L"SteamPath", nullptr, &type, reinterpret_cast<BYTE*>(value), &size) == ERROR_SUCCESS) roots.emplace_back(value);
                size = sizeof(value); if (RegQueryValueExW(key, L"InstallPath", nullptr, &type, reinterpret_cast<BYTE*>(value), &size) == ERROR_SUCCESS) roots.emplace_back(value);
                RegCloseKey(key);
            }
        }
        // Steam libraryfolders.vdf is the authoritative list when the game is
        // installed on another drive. Keep parsing deliberately small and
        // tolerant: only the quoted path values are needed here.
        // Only the actual Steam roots own libraryfolders.vdf. Newly discovered
        // library paths must not be parsed again: they may contain the same VDF
        // entries and otherwise grow this vector forever before the window is
        // even created.
        const size_t steamRootCount = roots.size();
        for (size_t index = 0; index < steamRootCount; ++index) {
            std::wstring vdf = ReadUtf8(Join(Join(roots[index], L"steamapps"), L"libraryfolders.vdf"));
            size_t cursor = 0;
            while ((cursor = vdf.find(L"\"path\"", cursor)) != std::wstring::npos) {
                cursor = vdf.find(L'"', cursor + 6); if (cursor == std::wstring::npos) break;
                size_t end = vdf.find(L'"', cursor + 1); if (end == std::wstring::npos) break;
                std::wstring path = vdf.substr(cursor + 1, end - cursor - 1); size_t slash = 0; while ((slash = path.find(L"\\\\", slash)) != std::wstring::npos) { path.replace(slash, 2, L"\\"); ++slash; }
                const auto samePath = [&path](const std::wstring& existing) { return _wcsicmp(existing.c_str(), path.c_str()) == 0; };
                if (std::find_if(roots.begin(), roots.end(), samePath) == roots.end()) roots.push_back(path);
                cursor = end + 1;
            }
        }
        for (const auto& candidate : roots) {
            std::wstring game = Join(Join(candidate, L"steamapps\\common"), L"VA-11 HALL-A");
            if (IsGameDir(game)) return game;
        }
        return {};
    }

    void State(const std::wstring& status, bool busy) {
        if (!webview) return;
        std::wstring config = Join(installDir, L"open-shift.toml");
        std::wstring thinking = ReadThinking(config);
        std::wstring js = L"window.setState({steam:" + JsonString(selectedGame) + L",copy:" + JsonString(gameCopy) + L",status:" + JsonString(status) + L",busy:" + (busy ? L"true" : L"false") + L",startDisabled:" + (FileExists(Join(installDir, L"Start-Open-Shift.ps1")) ? L"false" : L"true") + L",thinking:" + JsonString(thinking) + L",thinkingAvailable:" + (FileExists(config) ? L"true" : L"false") + L",packageVersion:" + JsonString(packageVersion) + L",keyConfigured:" + (FileExists(Join(installDir, L"api-key.dpapi")) ? L"true" : L"false") + L",installReady:" + (FileExists(Join(installDir, L"Start-Open-Shift.ps1")) ? L"true" : L"false") + L"});";
        webview->ExecuteScript(js.c_str(), nullptr);
    }

    std::wstring ReadThinking(const std::wstring& path) {
        std::wstring text = ReadUtf8(path);
        size_t p = text.find(L"thinking");
        if (p == std::wstring::npos) return L"disabled";
        p = text.find(L'"', p);
        if (p == std::wstring::npos) return L"disabled";
        size_t end = text.find(L'"', p + 1);
        return end == std::wstring::npos ? L"disabled" : text.substr(p + 1, end - p - 1);
    }

    void Browse() {
        BROWSEINFOW info{}; info.hwndOwner = window; info.lpszTitle = L"选择包含 data.win 的 VA-11 HALL-A Steam 游戏目录"; info.ulFlags = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE;
        PIDLIST_ABSOLUTE list = SHBrowseForFolderW(&info);
        if (!list) return;
        wchar_t path[MAX_PATH]{};
        if (SHGetPathFromIDListW(list, path)) { selectedGame = path; State(L"已选择 Steam 游戏目录。", false); }
        CoTaskMemFree(list);
    }

    void SaveKey(const std::wstring& value) {
        if (value.empty()) { State(L"DeepSeek API Key 不能为空。", false); return; }
        CreateDirectoryW(installDir.c_str(), nullptr);
        int size = WideCharToMultiByte(CP_UTF8, 0, value.data(), static_cast<int>(value.size()), nullptr, 0, nullptr, nullptr);
        std::string utf8(size, '\0');
        WideCharToMultiByte(CP_UTF8, 0, value.data(), static_cast<int>(value.size()), utf8.data(), size, nullptr, nullptr);
        DATA_BLOB plain{ static_cast<DWORD>(utf8.size()), reinterpret_cast<BYTE*>(utf8.data()) }, encrypted{};
        if (!CryptProtectData(&plain, L"OPEN SHIFT API Key", nullptr, nullptr, nullptr, 0, &encrypted)) { State(L"API Key 加密保存失败。", false); return; }
        std::ofstream file(Join(installDir, L"api-key.dpapi"), std::ios::binary | std::ios::trunc); file.write(reinterpret_cast<char*>(encrypted.pbData), encrypted.cbData); bool saved = file.good(); file.close(); LocalFree(encrypted.pbData); if (!saved) { State(L"API Key 写入失败，请检查安装目录权限。", false); return; }
        State(L"DeepSeek API Key 已为当前 Windows 用户加密保存。", false);
    }

    std::wstring Quote(const std::wstring& value) {
        // CommandLineToArgvW-compatible quoting. Paths may contain quotes or
        // trailing backslashes; simply wrapping them is not safe.
        std::wstring result = L"\""; size_t slashes = 0;
        for (wchar_t c : value) {
            if (c == L'\\') { ++slashes; continue; }
            if (c == L'\"') { result.append(slashes * 2 + 1, L'\\'); result += L'\"'; slashes = 0; continue; }
            result.append(slashes, L'\\'); slashes = 0; result += c;
        }
        result.append(slashes * 2, L'\\'); result += L'\"'; return result;
    }

    void StartPowerShell(const std::wstring& script, const std::wstring& args, const std::wstring& log) {
        if (process) { State(L"当前操作仍在进行，请稍候。", true); return; }
        CreateDirectoryW(installDir.c_str(), nullptr);
        SECURITY_ATTRIBUTES security{ sizeof(SECURITY_ATTRIBUTES), nullptr, TRUE }; CreatePipe(&output, &outputWrite, &security, 0); SetHandleInformation(output, HANDLE_FLAG_INHERIT, 0);
        std::wstring command = L"powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File " + Quote(script) + L" " + args;
        std::vector<wchar_t> commandLine(command.begin(), command.end()); commandLine.push_back(L'\0');
        STARTUPINFOW startup{ sizeof(startup) }; startup.dwFlags = STARTF_USESTDHANDLES; startup.hStdOutput = outputWrite; startup.hStdError = outputWrite;
        PROCESS_INFORMATION info{};
        if (!CreateProcessW(nullptr, commandLine.data(), nullptr, nullptr, TRUE, CREATE_NO_WINDOW, nullptr, root.c_str(), &startup, &info)) { ClosePipe(); State(L"无法启动 PowerShell 子进程。", false); return; }
        CloseHandle(info.hThread); CloseHandle(outputWrite); outputWrite = nullptr; process = info.hProcess; activeLog = log; installing = script.find(L"install-open-shift.ps1") != std::wstring::npos; launchConfirmed = false;
        State(installing ? L"正在校验 Steam 文件并生成隔离副本..." : L"正在使用 DeepSeek 准备 Open Shift 营业日...", true);
    }

    HANDLE outputWrite = nullptr;
    void ClosePipe() { if (output) { CloseHandle(output); output = nullptr; } if (outputWrite) { CloseHandle(outputWrite); outputWrite = nullptr; } }

    void Poll() {
        if (!process) return;
        DWORD available = 0; if (PeekNamedPipe(output, nullptr, 0, nullptr, &available, nullptr) && available) {
            std::string bytes(available, '\0'); DWORD read = 0; ReadFile(output, bytes.data(), available, &read, nullptr); bytes.resize(read);
            std::ofstream log(activeLog, std::ios::binary | std::ios::app); log.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
            std::string lower = bytes; std::transform(lower.begin(), lower.end(), lower.begin(), [](char c) { return static_cast<char>(tolower(static_cast<unsigned char>(c))); });
            if (lower.find("day is ready") != std::string::npos) State(L"OPEN SHIFT 已准备完成，正在启动 VA-11 HALL-A...", true);
            if (lower.find("run_start") != std::string::npos || lower.find("entering main loop") != std::string::npos) { launchConfirmed = true; State(L"游戏已启动，可以关闭此窗口。", true); }
        }
        if (WaitForSingleObject(process, 0) != WAIT_OBJECT_0) return;
        DWORD code = 1; GetExitCodeProcess(process, &code); bool markerReady = installing && FileExists(marker); bool ok = installing ? ((code == 0 || markerReady) && FileExists(Join(installDir, L"Start-Open-Shift.ps1"))) : (code == 0 || launchConfirmed);
        CloseHandle(process); process = nullptr; ClosePipe();
        if (markerReady) DeleteFileW(marker.c_str());
        if (ok && installing) { if (!pendingKey.empty()) SaveKey(pendingKey); pendingKey.clear(); State(L"安装完成。Steam 原版文件未被修改。", false); }
        else State(ok ? L"游戏会话已结束。" : (installing ? L"安装失败，请打开日志查看诊断信息。" : L"启动失败，请打开日志查看诊断信息。"), false);
    }

    void Install(const std::wstring& steam, const std::wstring& key) {
        selectedGame = steam; if (!IsGameDir(selectedGame)) { State(L"请选择包含 data.win 的 VA-11 HALL-A Steam 游戏目录。", false); return; }
        pendingKey = key; marker = GetTempPathString() + L"open-shift-install-" + std::to_wstring(GetCurrentProcessId()) + L".complete";
        StartPowerShell(Join(root, L"packaging\\install-open-shift.ps1"), L"-SteamGameDir " + Quote(selectedGame) + L" -InstallDir " + Quote(installDir) + L" -GameCopyDir " + Quote(gameCopy) + L" -CompletionMarker " + Quote(marker) + L" -SkipCredential -SkipShortcut", Join(installDir, L"installer.log"));
    }

    std::wstring GetTempPathString() { wchar_t path[MAX_PATH]{}; GetTempPathW(MAX_PATH, path); return std::wstring(path).append(L"open-shift-").append(std::to_wstring(GetCurrentProcessId())); }

    void StartGame() { std::wstring launcher = Join(installDir, L"Start-Open-Shift.ps1"); if (!FileExists(launcher)) { State(L"请先安装 Open Shift。", false); return; } StartPowerShell(launcher, {}, Join(installDir, L"launcher.log")); }

    void OpenReleasePage(const std::wstring& requested) {
        // The URL normally comes from GitHub's API. Keep an allowlist here so
        // a malformed WebView message cannot open an arbitrary site/protocol.
        const std::wstring prefix = L"https://github.com/KaiDecker/va11-open-shift/releases/";
        const std::wstring fallback = prefix + L"latest";
        const std::wstring target = requested.rfind(prefix, 0) == 0 ? requested : fallback;
        ShellExecuteW(window, L"open", target.c_str(), nullptr, nullptr, SW_SHOWNORMAL);
    }

    void CheckUpdates() {
        State(L"正在打开 GitHub 版本页（离线时不会影响游戏）...", false);
        OpenReleasePage(L"https://github.com/KaiDecker/va11-open-shift/releases/latest");
    }

    void ExportDiagnostics() {
        wchar_t path[MAX_PATH] = L"open-shift-diagnostics.txt";
        OPENFILENAMEW dialog{ sizeof(dialog) }; dialog.hwndOwner = window; dialog.lpstrFile = path; dialog.nMaxFile = MAX_PATH; dialog.lpstrFilter = L"Text files\0*.txt\0All files\0*.*\0"; dialog.lpstrDefExt = L"txt"; dialog.Flags = OFN_OVERWRITEPROMPT | OFN_PATHMUSTEXIST;
        if (!GetSaveFileNameW(&dialog)) return;
        std::wostringstream report;
        report << L"OPEN SHIFT diagnostic export\n" << L"install_dir=" << installDir << L"\n" << L"game_copy_dir=" << gameCopy << L"\n\n";
        for (const auto& name : { L"launcher.log", L"timing.log", L"dialogue.log" }) {
            std::wstring source = Join(installDir, name); report << L"--- " << name << L" ---\n";
            std::wstring content = ReadUtf8(source); if (content.empty()) report << L"(missing)\n"; else report << content << L"\n";
        }
        WriteUtf8(path, report.str()); State(L"诊断日志已导出（未包含 API Key）。", false);
    }

    void Thinking(const std::wstring& mode) {
        if (mode != L"enabled" && mode != L"balanced" && mode != L"disabled") { State(L"DeepSeek Thinking 模式无效。", false); return; }
        std::wstring path = Join(installDir, L"open-shift.toml"), text = ReadUtf8(path); size_t section = text.find(L"[provider]");
        size_t sectionEnd = section == std::wstring::npos ? std::wstring::npos : text.find(L"\n[", section + 1);
        if (section == std::wstring::npos) { State(L"运行配置中缺少 provider.thinking 设置，请执行安装 / 修复。", false); return; }
        if (sectionEnd == std::wstring::npos) sectionEnd = text.size();
        size_t position = section, quote = std::wstring::npos, end = std::wstring::npos; int matches = 0;
        while ((position = text.find(L"thinking", position)) != std::wstring::npos && position < sectionEnd) {
            size_t lineStart = text.rfind(L'\n', position); lineStart = lineStart == std::wstring::npos || lineStart < section ? section + 1 : lineStart + 1;
            size_t equals = text.find(L'=', position + 8), lineEnd = text.find(L'\n', position);
            if (lineEnd == std::wstring::npos || lineEnd > sectionEnd) lineEnd = sectionEnd;
            std::wstring line = text.substr(lineStart, lineEnd - lineStart); size_t name = line.find(L"thinking");
            if (name != std::wstring::npos && line.find(L'=', name + 8) != std::wstring::npos) {
                size_t first = line.find(L'"', name); size_t second = first == std::wstring::npos ? first : line.find(L'"', first + 1);
                std::wstring value = first == std::wstring::npos || second == std::wstring::npos ? L"" : line.substr(first + 1, second - first - 1);
                if (value != L"enabled" && value != L"balanced" && value != L"disabled" && value != L"default") { State(L"运行配置中的 thinking 设置无法安全修改。", false); return; }
                ++matches; quote = first == std::wstring::npos ? quote : lineStart + first; end = second == std::wstring::npos ? end : lineStart + second;
            }
            position = lineEnd + 1;
        }
        if (matches != 1 || quote == std::wstring::npos || end == std::wstring::npos) { State(L"运行配置中的 thinking 设置数量或格式不正确。", false); return; }
        text.replace(quote + 1, end - quote - 1, mode);
        std::wstring temporary = path + L"." + std::to_wstring(GetCurrentProcessId()) + L".tmp";
        WriteUtf8(temporary, text);
        if (!MoveFileExW(temporary.c_str(), path.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) { DeleteFileW(temporary.c_str()); State(L"运行配置保存失败。", false); return; }
        State(L"DeepSeek 模式已切换，下次生成时生效。", false);
    }

    void Message(const std::wstring& message) {
        std::wstring action = JsonValue(message, L"action");
        if (process && action != L"logs") { State(L"当前操作仍在进行，请稍候。", true); return; }
        if (action == L"browse") Browse(); else if (action == L"saveKey") SaveKey(JsonValue(message, L"value")); else if (action == L"install") Install(JsonValue(message, L"steam"), JsonValue(message, L"key")); else if (action == L"start") StartGame(); else if (action == L"thinking") Thinking(JsonValue(message, L"value")); else if (action == L"steamChanged") { selectedGame = JsonValue(message, L"value"); State(L"已更新 Steam 游戏目录。", false); } else if (action == L"logs") { std::wstring log = activeLog.empty() ? Join(installDir, L"launcher.log") : activeLog; if (FileExists(log)) ShellExecuteW(window, L"open", L"notepad.exe", Quote(log).c_str(), nullptr, SW_SHOWNORMAL); else State(L"目前还没有可打开的日志。", false); } else if (action == L"updateOpen") CheckUpdates(); else if (action == L"export") ExportDiagnostics(); else if (action == L"uninstall") { if (MessageBoxW(window, L"确定删除 OPEN SHIFT 的隔离实例吗？\n\nSteam 原版文件和玩家存档不会被删除。", L"OPEN SHIFT 卸载", MB_YESNO | MB_ICONQUESTION | MB_DEFBUTTON2) != IDYES) return; std::wstring script = Join(installDir, L"packaging\\uninstall-open-shift.ps1"); if (!FileExists(script)) { State(L"没有找到卸载脚本。", false); return; } ShellExecuteW(window, L"open", L"powershell.exe", (L"-NoProfile -ExecutionPolicy Bypass -File " + Quote(script) + L" -InstallDir " + Quote(installDir) + L" -WaitForProcessId " + std::to_wstring(GetCurrentProcessId())).c_str(), nullptr, SW_HIDE); PostMessageW(window, WM_CLOSE, 0, 0); }
    }
};

Launcher* g_app = nullptr;

LRESULT CALLBACK WindowProc(HWND hwnd, UINT message, WPARAM wParam, LPARAM lParam) {
    if (message == WM_CLOSE && g_app && g_app->process) {
        if (g_app->installing) { MessageBoxW(hwnd, L"安装仍在进行，请等待完成后再关闭窗口。", L"OPEN SHIFT", MB_OK | MB_ICONINFORMATION); return 0; }
        // Detach from the launch PowerShell process. It owns the bridge/game
        // lifetime and must continue running after this UI closes.
        CloseHandle(g_app->process); g_app->process = nullptr; g_app->ClosePipe();
    }
    if (message == WM_SIZE && g_app && g_app->controller) { RECT bounds{}; GetClientRect(hwnd, &bounds); g_app->controller->put_Bounds(bounds); }
    if (message == WM_TIMER && g_app) g_app->Poll();
    if (message == WM_DESTROY) { PostQuitMessage(0); return 0; }
    return DefWindowProcW(hwnd, message, wParam, lParam);
}

void InitializeBrowser(Launcher& app) {
    std::wstring userData = Join(app.installDir, L"webview-data"); CreateDirectoryW(userData.c_str(), nullptr);
    CreateCoreWebView2EnvironmentWithOptions(nullptr, userData.c_str(), nullptr, Callback<ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler>([&app](HRESULT error, ICoreWebView2Environment* environment) -> HRESULT {
        if (FAILED(error) || !environment) { MessageBoxW(app.window, L"无法初始化 Microsoft Edge WebView2 Runtime。请安装 WebView2 Evergreen Runtime 后重试。", L"OPEN SHIFT", MB_OK | MB_ICONERROR); DestroyWindow(app.window); return error; }
        return environment->CreateCoreWebView2Controller(app.window, Callback<ICoreWebView2CreateCoreWebView2ControllerCompletedHandler>([&app](HRESULT result, ICoreWebView2Controller* controller) -> HRESULT {
            if (FAILED(result) || !controller) { MessageBoxW(app.window, L"WebView2 窗口创建失败，请确认发行包完整。", L"OPEN SHIFT", MB_OK | MB_ICONERROR); DestroyWindow(app.window); return result; }
            app.controller = controller; controller->get_CoreWebView2(&app.webview);
            app.webview->add_WebMessageReceived(Callback<ICoreWebView2WebMessageReceivedEventHandler>([&app](ICoreWebView2*, ICoreWebView2WebMessageReceivedEventArgs* args) -> HRESULT { LPWSTR raw = nullptr; if (SUCCEEDED(args->TryGetWebMessageAsString(&raw))) { app.Message(raw); CoTaskMemFree(raw); } return S_OK; }).Get(), nullptr);
            RECT bounds{}; GetClientRect(app.window, &bounds); controller->put_Bounds(bounds);
            std::wstring page = Join(app.root, L"packaging\\webview\\index.html"), uri = L"file:///" + page; std::replace(uri.begin(), uri.end(), L'\\', L'/'); app.webview->Navigate(uri.c_str()); app.State(L"就绪。Steam 原版文件保持只读。", false); return S_OK;
        }).Get());
    }).Get());
}

} // namespace

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE, PWSTR, int show) {
    CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    Launcher app; g_app = &app;
    WNDCLASSW klass{}; klass.hInstance = instance; klass.lpfnWndProc = WindowProc; klass.lpszClassName = L"OpenShiftNativeLauncher"; klass.hCursor = LoadCursor(nullptr, IDC_ARROW); HICON icon = static_cast<HICON>(LoadImageW(nullptr, Join(app.root, L"OpenShift.ico").c_str(), IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)); klass.hIcon = icon; RegisterClassW(&klass);
    RECT size{ 0, 0, 600, 820 }; AdjustWindowRect(&size, WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX, FALSE);
    app.window = CreateWindowExW(0, klass.lpszClassName, L"OPEN SHIFT", WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX, CW_USEDEFAULT, CW_USEDEFAULT, size.right - size.left, size.bottom - size.top, nullptr, nullptr, instance, nullptr);
    ShowWindow(app.window, show); SetTimer(app.window, 1, 250, nullptr); InitializeBrowser(app);
    MSG message{}; while (GetMessageW(&message, nullptr, 0, 0) > 0) { TranslateMessage(&message); DispatchMessageW(&message); }
    if (app.process) CloseHandle(app.process); app.ClosePipe(); KillTimer(app.window, 1); CoUninitialize(); return 0;
}
