; Inno Setup 6.3+ script for HyperX Battery.
;
; Build with:  .\build.ps1 -Installer          (compiles the .exe first, then this script)
; or by hand:  ISCC.exe /DAppVersion=1.1.0 installer\HyperXBattery.iss
; The version is NOT written here: build.ps1 reads it from hyperx_battery\__init__.py.
;
; Result: dist\HyperXBattery-Setup-<version>.exe  (per-user install, no UAC prompt).
;
; Things that must stay in sync with the Python code (tests\test_installer.py checks them):
;   AppMutex      == hyperx_battery.__main__.MUTEX_NAME
;   Run value     == hyperx_battery.autostart.VALUE_NAME / RUN_KEY / build_command(frozen=True)
;   Data folder   == hyperx_battery.config.APP_NAME under %APPDATA%
; This file must be saved as UTF-8 *with BOM* (it contains non-ASCII characters).

#ifndef AppVersion
  #error Pass the version: ISCC.exe /DAppVersion=x.y.z  (build.ps1 -Installer does it for you)
#endif

#define AppName      "HyperX Battery"
#define AppExe       "HyperXBattery.exe"
#define AppPublisher "Franco Roldán"
#define AppURL       "https://github.com/franco314/hyperx-battery"
; Never change this GUID: it is what makes a new setup upgrade the previous install.
#define AppGuid      "EB7549B1-D2D2-499F-B05B-F5970F7343E1"
; Same name (and "Local\" session namespace) the app creates in hyperx_battery\__main__.py.
#define AppMutex     "Local\HyperXBatteryTray"

[Setup]
AppId={{{#AppGuid}}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
AppCopyright=Copyright (c) 2026 {#AppPublisher}
VersionInfoVersion={#AppVersion}
VersionInfoDescription={#AppName} Setup

; Per-user install: no admin, no UAC, HKCU only.
PrivilegesRequired=lowest
DefaultDirName={userpf}\{#AppName}
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

; Running instance: the app owns this mutex while it runs. Setup / uninstall detect it
; and the [Code] below offers to close it (see InitializeSetup / InitializeUninstall).
AppMutex={#AppMutex}
SetupMutex=HyperXBatterySetup
CloseApplications=yes
RestartApplications=no

UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
SetupIconFile=..\build\HyperXBattery.ico

OutputDir=..\dist
OutputBaseFilename=HyperXBattery-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ShowLanguageDialog=auto

[Languages]
; The first entry is the fallback when the system language is neither Spanish nor English.
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
spanish.StartupTask=Iniciar con Windows
spanish.OptionsGroup=Opciones:
spanish.LaunchApp=Abrir %1
spanish.CloseAppPrompt=%1 se está ejecutando.%n%nHay que cerrarlo para continuar. ¿Cerrarlo ahora?
spanish.CloseAppFailed=No se pudo cerrar %1.%n%nCiérralo desde el ícono de la bandeja (clic derecho, Salir) y vuelve a intentarlo.
spanish.DeleteDataPrompt=¿Quieres borrar también tus datos (historial y configuración)?%n%nCarpeta: %1
english.StartupTask=Start with Windows
english.OptionsGroup=Options:
english.LaunchApp=Open %1
english.CloseAppPrompt=%1 is running.%n%nIt has to be closed to continue. Close it now?
english.CloseAppFailed=Could not close %1.%n%nQuit it from its tray icon (right-click, Exit) and try again.
english.DeleteDataPrompt=Do you also want to delete your data (history and settings)?%n%nFolder: %1

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startup"; Description: "{cm:StartupTask}"; GroupDescription: "{cm:OptionsGroup}"

[Files]
Source: "..\dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; Exactly what hyperx_battery\autostart.py writes (frozen build): the quoted path of the exe.
; That way the app's own "Iniciar con Windows" menu entry shows it as on and can turn it off.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "HyperXBattery"; ValueData: """{app}\{#AppExe}"""; Tasks: startup; Check: StartupWanted; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchApp,{#AppName}}"; Flags: nowait postinstall skipifsilent runasoriginaluser
; Silent upgrades (/VERYSILENT) close the running app, so start it again.
Filename: "{app}\{#AppExe}"; Flags: nowait runasoriginaluser; Check: RelaunchAfterSilentInstall

[Code]
const
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';
  RunValue = 'HyperXBattery';
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{{#AppGuid}}_is1';

var
  AppWasRunning: Boolean;     // the app was running when Setup started
  PreviousInstall: Boolean;   // this AppId is already installed (this run is an upgrade / reinstall)
  StartupSynced: Boolean;

{ True while the app's single-instance mutex exists. }
function IsAppMutexHeld: Boolean;
begin
  Result := CheckForMutexes('{#AppMutex}');
end;

{ True while any HyperXBattery.exe process of this user exists. The mutex alone is not enough:
  a onefile PyInstaller build spends its first seconds unpacking before it creates the mutex,
  and a --simulate-percent run uses a different one, yet both lock the exe. }
function IsAppProcessRunning: Boolean;
var
  OutFile: String;
  Output: AnsiString;
  ResultCode: Integer;
begin
  Result := False;
  OutFile := ExpandConstant('{tmp}\hxb-tasklist.txt');
  DeleteFile(OutFile);
  if Exec(ExpandConstant('{cmd}'),
          '/C tasklist /FI "IMAGENAME eq {#AppExe}" /FI "USERNAME eq %USERDOMAIN%\%USERNAME%" /NH /FO CSV > "' + OutFile + '"',
          '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    if LoadStringFromFile(OutFile, Output) then
      Result := Pos(Lowercase('{#AppExe}'), Lowercase(String(Output))) > 0;
end;

function IsAppRunning: Boolean;
begin
  Result := IsAppMutexHeld or IsAppProcessRunning;
end;

function WaitForAppExit(Milliseconds: Integer): Boolean;
var
  Waited: Integer;
begin
  Waited := 0;
  while IsAppRunning and (Waited < Milliseconds) do
  begin
    Sleep(250);
    Waited := Waited + 250;
  end;
  Result := not IsAppRunning;
end;

{ Ask the app to quit (WM_CLOSE, so it shuts down cleanly); if it doesn't within 5 s, kill it.
  A onefile PyInstaller build runs two processes called HyperXBattery.exe (bootloader + child);
  /T takes both down. }
function StopApp: Boolean;
var
  ResultCode: Integer;
begin
  Log('Closing {#AppName}...');
  Exec(ExpandConstant('{cmd}'), '/C taskkill /IM {#AppExe} /FI "USERNAME eq %USERDOMAIN%\%USERNAME%"',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  if not WaitForAppExit(5000) then
  begin
    Log('It did not quit by itself; killing it.');
    Exec(ExpandConstant('{cmd}'), '/C taskkill /F /T /IM {#AppExe} /FI "USERNAME eq %USERDOMAIN%\%USERNAME%"',
         '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    WaitForAppExit(5000);
  end;
  Result := not IsAppRunning;
  Log(Format('{#AppName} closed: %d', [Result]));
end;

{ Silent runs close the app without asking (that is what an unattended upgrade needs). }
function EnsureAppClosed(Silent: Boolean): Boolean;
begin
  Result := True;
  if not IsAppRunning then
    Exit;
  if not Silent then
    if MsgBox(FmtMessage(CustomMessage('CloseAppPrompt'), ['{#AppName}']), mbConfirmation, MB_YESNO) <> IDYES then
    begin
      Result := False;
      Exit;
    end;
  Result := StopApp;
  if not Result then
    MsgBox(FmtMessage(CustomMessage('CloseAppFailed'), ['{#AppName}']), mbError, MB_OK);
end;

function InitializeSetup: Boolean;
begin
  PreviousInstall := RegKeyExists(HKCU, UninstallKey);
  AppWasRunning := IsAppRunning;
  Log(Format('PreviousInstall=%d AppWasRunning=%d', [PreviousInstall, AppWasRunning]));
  Result := EnsureAppClosed(WizardSilent);
end;

function RelaunchAfterSilentInstall: Boolean;
begin
  Result := WizardSilent and AppWasRunning;
end;

{ The Run value is shared with the app's own menu toggle, so on an upgrade the installer must
  not undo what the user chose there:
  - interactive: the "Iniciar con Windows" checkbox starts as the real current state;
  - silent (no explicit /TASKS or /MERGETASKS): the value is only rewritten if it exists. }
function StartupWanted: Boolean;
begin
  Result := True;
  if WizardSilent and PreviousInstall
     and (ExpandConstant('{param:TASKS|}') = '') and (ExpandConstant('{param:MERGETASKS|}') = '') then
    Result := RegValueExists(HKCU, RunKey, RunValue);
  Log(Format('StartupWanted=%d (TASKS="%s" MERGETASKS="%s")', [Result, ExpandConstant('{param:TASKS|}'), ExpandConstant('{param:MERGETASKS|}')]));
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  { Setup also walks through this page in silent mode, where /TASKS and StartupWanted decide. }
  if (CurPageID = wpSelectTasks) and (not StartupSynced) and (not WizardSilent) then
  begin
    StartupSynced := True;
    if PreviousInstall then
    begin
      if RegValueExists(HKCU, RunKey, RunValue) then
        WizardSelectTasks('startup')
      else
        WizardSelectTasks('!startup');
    end;
  end;
end;

function StartupCommand: String;
begin
  Result := '"' + ExpandConstant('{app}\{#AppExe}') + '"';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Current: String;
begin
  if CurStep = ssInstall then
    Log(Format('Tasks: startup=%d desktopicon=%d', [WizardIsTaskSelected('startup'), WizardIsTaskSelected('desktopicon')]));
  { Reinstall with the box unticked: turn autostart off, but only if it points at this install. }
  if (CurStep = ssPostInstall) and (not WizardSilent) and PreviousInstall
     and (not WizardIsTaskSelected('startup')) then
    if RegQueryStringValue(HKCU, RunKey, RunValue, Current) and (CompareText(Current, StartupCommand) = 0) then
      RegDeleteValue(HKCU, RunKey, RunValue);
end;

function InitializeUninstall: Boolean;
begin
  Result := EnsureAppClosed(UninstallSilent);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Current, DataDir: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    { uninsdeletevalue only covers a value written by Setup; the app's own menu may have
      created it later. Remove it too, but only if it points at this install. }
    if RegQueryStringValue(HKCU, RunKey, RunValue, Current) and (CompareText(Current, StartupCommand) = 0) then
      RegDeleteValue(HKCU, RunKey, RunValue);
  end
  else if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{userappdata}\HyperXBattery');
    { Silent uninstalls always keep the user's data. The default button is "No". }
    if (not UninstallSilent) and DirExists(DataDir) then
      if MsgBox(FmtMessage(CustomMessage('DeleteDataPrompt'), [DataDir]), mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
