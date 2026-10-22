; 历史相似检索系统 —— 安装包脚本（Inno Setup 6）
;
; 编译：  ISCC.exe packaging\histsim.iss
; 产物：  packaging\installer\Histsim_Setup_v2.1.0.exe
;
; 设计取舍：
; * 只打包 PyInstaller 的 onedir 产物（dist\Histsim），不做 onefile；
; * PrivilegesRequired=lowest + 装到 %LOCALAPPDATA%\Programs\Histsim：
;   普通用户免 UAC 即可安装，程序数据另存 %LOCALAPPDATA%\Histsim；
; * exe 名保持 ASCII（Histsim.exe），面向用户的快捷方式用中文；
; * 卸载只删程序文件，保留用户数据目录（检索结果/索引库），避免误删成果。

#define AppName "历史相似检索系统"
#define AppNameEn "Histsim"
#define AppVersion "2.1.0"

[Setup]
AppId={{8F3A61C4-7B2E-4E5D-9C1A-2D6E5F0B4A73}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
DefaultDirName={localappdata}\Programs\Histsim
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=installer
OutputBaseFilename=Histsim_Setup_v{#AppVersion}
SetupIconFile=assets\app.ico
UninstallDisplayIcon={app}\Histsim.exe
UninstallDisplayName={#AppName} {#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
SourceDir=.
; 32 位系统也能装 exe，但 netCDF4/matplotlib 依赖只提供 64 位运行时
MinVersion=10.0

[Languages]
Name: "chinese"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："

[Files]
; 冻结产物：exe 与 _internal（含 matplotlib、netCDF4、省界底图、前端静态资源）
Source: "dist\Histsim\Histsim.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\Histsim\_internal\*"; DestDir: "{app}\_internal"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

; 系统说明书（PDF + Word），安装到 docs 子目录
Source: "manual\历史相似检索系统说明书.pdf"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "manual\历史相似检索系统说明书.docx"; DestDir: "{app}\docs"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\Histsim.exe"; WorkingDir: "{app}"
Name: "{group}\停止{#AppName}"; Filename: "{app}\Histsim.exe"; Parameters: "--stop"; \
    WorkingDir: "{app}"
Name: "{group}\系统说明书（PDF）"; Filename: "{app}\docs\历史相似检索系统说明书.pdf"
Name: "{group}\卸载{#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\Histsim.exe"; WorkingDir: "{app}"; \
    Tasks: desktopicon

[Run]
Description: "立即启动{#AppName}"; Filename: "{app}\Histsim.exe"; WorkingDir: "{app}"; \
    Flags: postinstall nowait skipifsilent

[UninstallRun]
; 卸载前先停掉可能正在运行的后台服务（含它的 IO 子进程），否则文件删不掉
Filename: "{app}\Histsim.exe"; Parameters: "--stop"; Flags: runhidden; RunOnceId: "StopServer"

[Code]
{ 安装/升级前先结束正在运行的实例：后台服务常驻，不先停会导致文件占用无法覆盖 }
function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  Exec('taskkill.exe', '/IM Histsim.exe /T /F', '', SW_HIDE,
       ewWaitUntilTerminated, ResultCode);
  Result := True;
end;

{ 卸载完成后提示用户数据目录的去留，避免"卸载即丢结果"或"重复安装残留" }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\Histsim');
    if DirExists(DataDir) then
      MsgBox('程序文件已卸载。' + #13#10 + #13#10 +
             '检索结果、索引库等用户数据保留在：' + #13#10 + DataDir + #13#10 + #13#10 +
             '如需彻底清除，请手动删除该目录。',
             mbInformation, MB_OK);
  end;
end;