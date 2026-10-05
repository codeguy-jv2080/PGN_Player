#define AppName "PGN Player"
#define AppVersion "1.0.3"
#ifndef SourceRoot
  #define SourceRoot ".."
#endif

[Setup]
AppId={{C017F825-2309-4DC1-BE98-7C88DBD514AA}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=PGN Player contributors
DefaultDirName={localappdata}\Programs\PGN Player
DefaultGroupName=PGN Player
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#SourceRoot}\dist
OutputBaseFilename=PGN Player Setup
SetupIconFile={#SourceRoot}\src\pgn_player\assets\app.ico
UninstallDisplayIcon={app}\PGN Player.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
RestartApplications=no
AppMutex=PGNPlayer.Installed
ChangesAssociations=yes
LicenseFile={#SourceRoot}\LICENSE

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked
Name: "pgnassociation"; Description: "Register PGN Player in Open with for .pgn files"; GroupDescription: "File associations:"; Flags: unchecked

[Files]
Source: "{#SourceRoot}\.build\payload\PGN Player\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceRoot}\packaging\installed.json"; DestDir: "{app}"; DestName: "edition.json"; Flags: ignoreversion
Source: "{#SourceRoot}\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}\docs\SHORTCUTS.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\PGN Player"; Filename: "{app}\PGN Player.exe"
Name: "{autodesktop}\PGN Player"; Filename: "{app}\PGN Player.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Classes\PGNPlayer.PGN"; ValueType: string; ValueName: ""; ValueData: "PGN chess game"; Flags: uninsdeletekey; Tasks: pgnassociation
Root: HKCU; Subkey: "Software\Classes\PGNPlayer.PGN\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\PGN Player.exe,0"; Tasks: pgnassociation
Root: HKCU; Subkey: "Software\Classes\PGNPlayer.PGN\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\PGN Player.exe"" ""%1"""; Tasks: pgnassociation
Root: HKCU; Subkey: "Software\Classes\.pgn\OpenWithProgids"; ValueType: string; ValueName: "PGNPlayer.PGN"; ValueData: ""; Flags: uninsdeletevalue; Tasks: pgnassociation

; No Run or UninstallDelete entries: installing never launches the app and
; uninstalling leaves the installed edition's separate user-data folder intact.

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Target, Marker, Config: String;
  RawConfig: AnsiString;
  Entry: TFindRec;
  HasContents: Boolean;
begin
  Result := '';
  Target := ExpandConstant('{app}');
  Marker := AddBackslash(Target) + 'edition.json';
  if FileExists(Marker) then
  begin
    if not LoadStringFromFile(Marker, RawConfig) then
    begin
      Result := 'Cannot read the edition marker in this folder. Choose a separate empty folder.';
      Exit;
    end;
    Config := Lowercase(String(RawConfig));
    StringChangeEx(Config, ' ', '', True);
    StringChangeEx(Config, #13, '', True);
    StringChangeEx(Config, #10, '', True);
    StringChangeEx(Config, #9, '', True);
    if Config <> '{"edition":"installed"}' then
      Result := 'This folder belongs to a portable or unrecognized edition. Choose a separate empty folder for the installed edition. Its files and data have not been changed.';
  end
  else if DirExists(Target) then
  begin
    HasContents := False;
    if FindFirst(AddBackslash(Target) + '*', Entry) then
    begin
      try
        repeat
          if (Entry.Name <> '.') and (Entry.Name <> '..') then
            HasContents := True;
        until not FindNext(Entry);
      finally
        FindClose(Entry);
      end;
    end;
    if HasContents then
      Result := 'This folder already contains files without an installed-edition marker. Choose a separate empty folder; existing files have not been changed.';
  end;
end;
