; Ascend Windows 安装器（Inno Setup）
; 编译: make_installer.sh 调用 ISCC.exe，/D 传入 Stage/Version/VerNum/OutDir/Icon

#ifndef MyAppName
#define MyAppName "Ascend"
#endif
#ifndef MyAppVersion
#define MyAppVersion "0.0.1"
#endif
#ifndef MyAppVerNum
#define MyAppVerNum "0.0.1"
#endif
#ifndef Stage
#define Stage "..\..\work\staging\Ascend-windows"
#endif
#ifndef OutDir
#define OutDir "..\..\..\dist\release"
#endif
#ifndef IconFile
#define IconFile ""
#endif

[Setup]
AppId={{E58F0D50-78BD-4BFF-A7AD-AB6C7DE78C6E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=Ascend Developers
DefaultDirName={autopf}\Ascend
DefaultGroupName=Ascend
OutputDir={#OutDir}
OutputBaseFilename=ascend-windows-setup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\ascend.exe
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"

[Files]
Source: "{#Stage}\ascend.exe"; DestDir: "{app}"
Source: "{#Stage}\ascend.pck"; DestDir: "{app}"
Source: "{#Stage}\server\*"; DestDir: "{app}\server"; Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Ascend"; Filename: "{app}\ascend.exe"
Name: "{autodesktop}\Ascend"; Filename: "{app}\ascend.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ascend.exe"; Description: "运行 Ascend"; Flags: nowait postinstall skipifsilent
