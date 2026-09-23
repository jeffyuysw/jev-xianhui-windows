; Inno Setup script for 先回 (XianHui, Windows)
; Build the app first (packaging\build_installer.bat does this), then compile:
;   iscc packaging\installer.iss

#define AppName "先回"
#define AppVersion "1.0.0"
#define AppPublisher "先回"
#define AppExeName "XianHui.exe"

[Setup]
AppId={{8F2A7C41-6B3D-4E15-9A2E-5D7C1E4B9F30}}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=output
OutputBaseFilename=XianHui-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayIcon={app}\{#AppExeName}
; 安装包本身与"添加/删除程序"列表中的图标
SetupIconFile=..\assets\app.ico

[Languages]
Name: "chinese"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"; Flags: checkedonce

[Files]
; The whole PyInstaller one-folder output.
Source: "..\dist\XianHui\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; IconFilename 显式指定，确保快捷方式图标取自应用 exe 内嵌图标
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\{#AppExeName}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "立即运行 {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Leave the user's config alone; only remove our own install dir contents.
Type: filesandordirs; Name: "{app}"
