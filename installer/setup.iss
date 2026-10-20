; Inno Setup script for Mike's Canon Scanner
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define MyAppName "Mike's Canon Scanner"
#define MyAppExe "MikesCanonScanner.exe"

[Setup]
AppId={{6A1F3C2E-9B4D-4E7A-8C21-5D0F7B3A9E44}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=Mike Ooi
AppPublisherURL=https://github.com/mike131419/mikes-canon-scanner
AppUpdatesURL=https://github.com/mike131419/mikes-canon-scanner/releases
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=Output
OutputBaseFilename=MikesCanonScanner-{#MyAppVersion}-Setup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[InstallDelete]
; remove files from the previous version so upgrades are clean
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\MikesCanonScanner\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
; normal install: offer to launch at the end
Filename: "{app}\{#MyAppExe}"; Description: "Start {#MyAppName}"; Flags: nowait postinstall skipifsilent
; silent install (in-app update): relaunch automatically
Filename: "{app}\{#MyAppExe}"; Flags: nowait skipifnotsilent
