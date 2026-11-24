#define MyAppName "Label Maker v2"
#ifndef MyAppVersion
  #define MyAppVersion "3.2.2"
#endif
#define MyAppPublisher "Label Maker"
#define MyAppExeName "LabelMaker_v2.exe"

[Setup]
AppId={{7D03F91F-A778-4F40-973F-EA8028098128}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=LabelMaker_v2_Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
; Updates from inside the app run silently: close the running app first.
CloseApplications=force
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
Source: "..\dist\LabelMaker_v2\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
; After a silent in-app update, reopen the app.
Filename: "{app}\{#MyAppExeName}"; Flags: nowait skipifnotsilent
