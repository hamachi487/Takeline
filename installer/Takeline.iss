; Inno Setup 6 script - optional installer around the standalone exe.
; build_windows.ps1 runs this automatically when Inno Setup is installed.
#define AppName "Takeline"
#define AppVersion "1.0.0"

[Setup]
AppId={{3F9B2C71-8E4A-4D6B-A1C5-7D20E9B46F18}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppName}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=Takeline-Setup
SetupIconFile=..\assets\Takeline.ico
UninstallDisplayIcon={app}\Takeline.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\Takeline.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD-PARTY-NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\Takeline.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\Takeline.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Takeline.exe"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
