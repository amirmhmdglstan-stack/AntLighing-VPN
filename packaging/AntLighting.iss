; ---------------------------------------------------------------------------
; Inno Setup script for AntLighting VPN.
;
; Produces a single compressed, self-extracting installer
; (AntLighting-VPN-Setup-<ver>.exe) with the standard "full options" wizard:
;   * choose install location
;   * start-menu group
;   * [x] Create a desktop shortcut      (uses the generated VPN icon)
;   * [x] Launch after install
; and full silent-install support (/SILENT, /VERYSILENT, /DIR=, /TASKS=) so it
; can be tested repeatedly and automated.
;
; Build (Inno Setup 6 must be installed):   iscc packaging/AntLighting.iss
; The CI workflow installs Inno Setup automatically.
; ---------------------------------------------------------------------------

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

#define AppName "AntLighting VPN"
#define AppExe  "AntLighting.exe"

[Setup]
; Stable AppId so upgrades replace the same installation.
AppId={{8F3A2C1D-5B6E-4F7A-9C2D-1E3F5A7B9C0D}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=AntLighting contributors
VersionInfoVersion={#AppVersion}.0
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
AllowNoIcons=yes
; Per-user install: no admin rights needed (the app only needs elevation for
; the optional TUN mode, requested at runtime).
PrivilegesRequired=lowest
OutputDir=installer-output
OutputBaseFilename=AntLighting-VPN-Setup-{#AppVersion}
SetupIconFile=shortcut.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
; Strong single-file compression.
Compression=lzma2/ultra64
SolidCompression=yes
DiskSpanning=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
ShowLanguageDialog=no
CloseApplications=force
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; The desktop-shortcut option the user asked for; the shortcut uses the
; generated VPN icon (installed as shortcut.ico).
Name: "desktopicon"; Description: "&Create a desktop shortcut"; GroupDescription: "Additional options:"; Flags: unchecked
Name: "quicklaunch"; Description: "Create a &Quick Launch shortcut"; GroupDescription: "Additional options:"; Flags: unchecked

[Files]
; The frozen application (exe + _internal + bundled xray/wintun/geo + licences).
Source: "..\dist\AntLighting\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; The brand icon, installed so shortcuts can reference it.
Source: "shortcut.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\shortcut.ico"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\shortcut.ico"; Tasks: desktopicon
Name: "{userappdata}\Microsoft\Internet Explorer\Quick Launch\{#AppName}"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\shortcut.ico"; Tasks: quicklaunch

[Run]
Filename: "{app}\{#AppExe}"; Description: "&Launch AntLighting VPN now"; Flags: nowait postinstall skipifsilent unchecked

[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
