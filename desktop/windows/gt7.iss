; GT7 Datalogger for Windows: the Inno Setup script (#199).
;
; Built by desktop/windows/package.ps1, which passes:
;   /DAppVersion=X.Y.Z[-rc.N]   the release version (required)
;   /DFileVersion=X.Y.Z         the numeric part, for the version resource
;   /DSourceDir=<onedir>        PyInstaller's desktop\dist\gt7-datalogger
;   /DOutputDir=<dir>           where GT7-Datalogger-Windows-x64-Setup.exe goes
;   /DSignTool=<name> plus /S<name>=<command>   only when signing; an unsigned
;                               local build leaves both out
;
; The install folder is the same for every version ({autopf}\GT7 Datalogger),
; because the firewall rules are keyed on the exe's path and must survive an
; update. The data folder (%LOCALAPPDATA%\GT7 Datalogger) is never touched:
; not on install, not on uninstall.

#ifndef AppVersion
  #error Pass the version: ISCC /DAppVersion=X.Y.Z gt7.iss
#endif
#ifndef FileVersion
  #define FileVersion AppVersion
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\gt7-datalogger"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif

#define AppName "GT7 Datalogger"
#define ExeName "gt7-datalogger.exe"
#define RepoURL "https://github.com/jbhoorasingh/gt7-datalogger"
#define RunKey "Software\Microsoft\Windows\CurrentVersion\Run"

[Setup]
; Never change the AppId: it is how an update finds the installed copy.
AppId={{68FC04AF-BB35-469E-AEDE-2B305441F0E1}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Jermaine Bhoorasingh
AppPublisherURL={#RepoURL}
AppSupportURL={#RepoURL}/issues
AppUpdatesURL={#RepoURL}/releases
AppCopyright=Copyright (c) Jermaine Bhoorasingh. MIT License.
VersionInfoVersion={#FileVersion}
VersionInfoProductVersion={#FileVersion}
VersionInfoProductTextVersion={#AppVersion}
VersionInfoDescription={#AppName} Setup

; Per-machine by default; the first page offers "Install for me only", and
; winget or a script can pass /CURRENTUSER (plan open decision 4).
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog commandline
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763

DefaultDirName={autopf}\{#AppName}
DisableDirPage=yes
UsePreviousAppDir=no
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableWelcomePage=no
WizardStyle=modern

OutputDir={#OutputDir}
OutputBaseFilename=GT7-Datalogger-Windows-x64-Setup
Compression=lzma2/max
SolidCompression=yes

UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#ExeName}
; PrepareToInstall stops the app itself, gracefully; Restart Manager is the
; fallback for anything still holding a file, and nothing is restarted.
CloseApplications=yes
RestartApplications=no
; The Start at login value is the signed-in user's, as the tray writes it.
UsedUserAreasWarning=no

#ifdef SignTool
SignTool={#SignTool}
SignedUninstaller=yes
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
WelcomeLabel2=This will install [name/ver] on your computer.%n%nGT7 Datalogger records the telemetry your PlayStation sends while you drive Gran Turismo 7, and shows it live and lap by lap in your browser.%n%nGT7 Datalogger is unofficial and not affiliated with Sony Interactive Entertainment or Polyphony Digital.%n%nYour recordings stay in your own folder and are kept if you uninstall.

[Tasks]
Name: "startatlogin"; Description: "Start GT7 Datalogger when I sign in"; Flags: unchecked
Name: "updatecheck"; Description: "Check for updates daily"
; Per-user installs only: per-machine installs always add the rules. A silent
; per-user install skips it; the tray's "Repair firewall" (`--firewall repair`,
; elevated) adds them later.
Name: "firewall"; Description: "Let the PlayStation and my phone reach it through Windows Firewall (asks for administrator approval)"; Check: PerUserAndInteractive

[InstallDelete]
; A new version's bundle replaces the old one whole: a module the new build
; no longer ships must not linger and be imported.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#ExeName}"; Parameters: "--open"; WorkingDir: "{app}"; Comment: "Open the GT7 Datalogger dashboard"

[Registry]
; The same value the tray's Start at login toggle writes (gt7_desktop/autostart.py).
Root: HKCU; Subkey: "{#RunKey}"; ValueType: string; ValueName: "{#AppName}"; ValueData: """{app}\{#ExeName}"" --background --login"; Tasks: startatlogin

[UninstallDelete]
Type: files; Name: "{app}\install-defaults.json"

[Run]
Filename: "{app}\{#ExeName}"; Parameters: "--firewall install"; StatusMsg: "Adding the Windows Firewall rules..."; Flags: runhidden waituntilterminated; Check: IsAdminInstallMode
Filename: "{app}\{#ExeName}"; Parameters: "--firewall install"; StatusMsg: "Adding the Windows Firewall rules..."; Verb: "runas"; Flags: shellexec runhidden waituntilterminated; Tasks: firewall
Filename: "{app}\{#ExeName}"; Parameters: "--open"; Description: "Launch GT7 Datalogger"; Flags: nowait postinstall skipifsilent runasoriginaluser

[UninstallRun]
; A per-user uninstall cannot delete rules an elevated step added; netsh then
; fails quietly and the rules stay, pointing at a file that no longer exists.
Filename: "{app}\{#ExeName}"; Parameters: "--firewall remove"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveFirewallRules"

[Code]
var
  ClosePage: TOutputMsgWizardPage;

function InstalledExe(): String;
begin
  Result := ExpandConstant('{app}\{#ExeName}');
end;

procedure InitializeWizard();
begin
  ClosePage := CreateOutputMsgPage(wpSelectTasks,
    'GT7 Datalogger will be closed',
    'Setup stops the running copy before it updates the files.',
    'If GT7 Datalogger is running, Setup closes it when you click Install. ' +
    'A session it is recording is closed and saved as it stands; the laps ' +
    'driven so far are kept.' + #13#10#13#10 +
    'Start it again when Setup finishes, or leave "Launch GT7 Datalogger" ticked.');
end;

function PerUserAndInteractive(): Boolean;
begin
  Result := not IsAdminInstallMode() and not WizardSilent();
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PageID = ClosePage.ID) and not FileExists(InstalledExe());
end;

{ `--quit --timeout 20` asks the running app to stop (WM_CLOSE to the PID in
  its lock record) and waits for it. It exits 0 when nothing was running. }
function StopRunningApp(AsOriginalUser: Boolean): Boolean;
var
  ResultCode: Integer;
  Started: Boolean;
begin
  Result := True;
  if not FileExists(InstalledExe()) then
    exit;
  { The app's lock record lives in the signed-in user's %LOCALAPPDATA%, so
    on install (possibly elevated as another account) ask as that user. }
  if AsOriginalUser then
    Started := ExecAsOriginalUser(InstalledExe(), '--quit --timeout 20', '',
      SW_HIDE, ewWaitUntilTerminated, ResultCode)
  else
    Started := Exec(InstalledExe(), '--quit --timeout 20', '',
      SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := Started and (ResultCode = 0);
  if not Result then
    Log(Format('--quit did not confirm a stop (started=%d, exit=%d)', [Ord(Started), ResultCode]));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if not StopRunningApp(True) then
    Result := 'GT7 Datalogger did not stop within 20 seconds. Quit it from its ' +
      'tray icon, then run Setup again.';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Json: String;
begin
  if CurStep = ssPostInstall then
  begin
    { The launcher's baseline for GT7_UPDATE_CHECK (#196); see server_env. }
    if WizardIsTaskSelected('updatecheck') then
      Json := '{"update_check": true}'
    else
      Json := '{"update_check": false}';
    if not SaveStringToFile(ExpandConstant('{app}\install-defaults.json'), Json + #13#10, False) then
      Log('could not write install-defaults.json');
  end;
end;

function InitializeUninstall(): Boolean;
begin
  Result := True;
  if FileExists(InstalledExe()) and not UninstallSilent() then
    if MsgBox('GT7 Datalogger will be closed if it is running. A session it is ' +
        'recording is closed and saved.' + #13#10#13#10 +
        'Your recordings are kept: uninstalling never removes your data folder.',
        mbConfirmation, MB_OKCANCEL) <> IDOK then
    begin
      Result := False;
      exit;
    end;
  if not StopRunningApp(False) then
    Log('the app may still be running; Restart Manager handles any file in use');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  { The login item, whether the installer's task or the tray's toggle wrote it. }
  if CurUninstallStep = usPostUninstall then
    RegDeleteValue(HKEY_CURRENT_USER, '{#RunKey}', '{#AppName}');
end;
