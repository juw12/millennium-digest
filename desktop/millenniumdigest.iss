; MillenniumDigest for Windows - per-user installer (no admin rights). build.py compiles it with:
;   iscc /DAppVersion=1.0.0 /DSourceDir=<dist\MillenniumDigest> /DOutputDir=<dist> /DIconFile=<build\millenniumdigest.ico> millenniumdigest.iss
; Installs to %LOCALAPPDATA%\Programs\MillenniumDigest, adds MillenniumDigest to the Start menu (and optionally the desktop) and lists it
; in Settings > Apps. The window keeps its data (what you saved in it) in %LOCALAPPDATA%\MillenniumDigest, which
; uninstalling leaves in place so a reinstall finds it again.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "dist\MillenniumDigest"
#endif
#ifndef OutputDir
  #define OutputDir "dist"
#endif
#ifndef IconFile
  #define IconFile "build\millenniumdigest.ico"
#endif

[Setup]
AppId={{E81247BA-698B-569E-A6DE-E1E1C366EEFB}
AppName=MillenniumDigest
AppVersion={#AppVersion}
AppVerName=MillenniumDigest {#AppVersion}
AppPublisher=Juwen Lie
AppPublisherURL=https://juw12.github.io/millennium-digest/
AppSupportURL=https://github.com/juw12/millennium-digest
DefaultDirName={autopf}\MillenniumDigest
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=MillenniumDigest-Windows-Setup
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\MillenniumDigest.exe
UninstallDisplayName=MillenniumDigest
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=no
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\MillenniumDigest"; Filename: "{app}\MillenniumDigest.exe"; Comment: "A weekly digest of new arXiv papers on the seven Millennium Prize"
Name: "{userdesktop}\MillenniumDigest"; Filename: "{app}\MillenniumDigest.exe"; Comment: "A weekly digest of new arXiv papers on the seven Millennium Prize"; Tasks: desktopicon

[Run]
Filename: "{app}\MillenniumDigest.exe"; Description: "{cm:LaunchProgram,MillenniumDigest}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\MillenniumDigest.exe"; Parameters: "--stop"; Flags: runhidden waituntilterminated; RunOnceId: "StopMillenniumDigest"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Result := '';
  { Updating: close an open MillenniumDigest window first so its files can be replaced. }
  if FileExists(ExpandConstant('{app}\MillenniumDigest.exe')) then
    Exec(ExpandConstant('{app}\MillenniumDigest.exe'), '--stop', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;
