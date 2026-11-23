; Inno Setup 6 script. Build: iscc installer.iss (see build.ps1)
#define AppName "Meeting Recorder"
#define AppVersion "1.2.0"
#ifndef DistDir
  #define DistDir "dist"
#endif
#ifndef OutDir
  #define OutDir "installer_out"
#endif

[Setup]
AppId={{B7C2D6B0-5F2A-4E7B-9C5A-6D1E3A7F0A11}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={autopf}\MeetingRecorder
DefaultGroupName={#AppName}
OutputDir={#OutDir}
OutputBaseFilename=MeetingRecorder-Setup
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\MeetingRecorder.exe
SetupIconFile=assets\icon.ico
LicenseFile=LICENSE

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Files]
Source: "{#DistDir}\MeetingRecorder\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\MeetingRecorder.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\MeetingRecorder.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; Flags: unchecked
Name: "autostart"; Description: "Start with Windows / Запускать вместе с Windows"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Meeting Recorder"; ValueData: """{app}\MeetingRecorder.exe"""; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\MeetingRecorder.exe"; Description: "Launch Meeting Recorder"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "taskkill"; Parameters: "/F /IM MeetingRecorder.exe"; Flags: runhidden; RunOnceId: "KillApp"
