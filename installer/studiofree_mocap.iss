; StudioFree MoCap per-user offline installer (Inno Setup 7.x).
; Distribute inside one ZIP beside its verified payload/ directory.
; The Python maintenance script uses the packaged offline control runtime.
; It never reads or removes project data.

#ifndef ProductVersion
  #error ProductVersion is required
#endif

[Setup]
AppId={{D377EBD6-3302-4AC4-A49E-7F4247FE2321}
AppName=StudioFree MoCap
AppVersion={#ProductVersion}
AppPublisher=Arcomade
DefaultDirName={localappdata}\Programs\VTA\Installer
DisableDirPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
#ifdef SignToolName
OutputBaseFilename=StudioFreeMoCap-{#ProductVersion}
#else
OutputBaseFilename=StudioFreeMoCap-{#ProductVersion}-unsigned-test
#endif
Compression=lzma2
SolidCompression=yes
CloseApplications=no
RestartApplications=no
SetupLogging=yes
UninstallLogging=yes
UninstallDisplayName=StudioFree MoCap
UsePreviousAppDir=yes
#ifdef SignToolName
SignTool={#SignToolName}
SignedUninstaller=yes
#endif

[Files]
Source: "{src}\payload\*"; DestDir: "{app}\payload"; Flags: external recursesubdirs createallsubdirs ignoreversion

[InstallDelete]
; A full replacement must remove A-only files before copying payload B.
Type: filesandordirs; Name: "{app}\payload"

[Registry]
Root: HKA; Subkey: "Software\StudioFree\MoCap"; ValueType: string; ValueName: "InstallerPython"; ValueData: "{app}\payload\control\python.exe"; Flags: uninsdeletevalue

[Code]
var
  BlenderPage: TInputFileWizardPage;

procedure InitializeWizard;
begin
  BlenderPage := CreateInputFilePage(wpWelcome,
    'Select Blender 5.2',
    'StudioFree MoCap is installed into your Blender user extension repository.',
    'Select the blender.exe for the Blender 5.2 installation you want to use. Blender is installed separately.');
  BlenderPage.Add('Blender executable:', 'Executables|*.exe', '.exe');
  BlenderPage.Values[0] := ExpandConstant('{autopf}\Blender Foundation\Blender 5.2\blender.exe');
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = BlenderPage.ID then begin
    if not FileExists(BlenderPage.Values[0]) then begin
      MsgBox('Select an installed Blender 5.2 blender.exe.', mbError, MB_OK);
      Result := False;
    end else if not FileExists(ExpandConstant('{src}\payload\control\python.exe')) then begin
      MsgBox('The offline StudioFree control runtime is missing. Extract the complete distribution ZIP and retry.', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ExitCode: Integer;
begin
  Result := '';
  NeedsRestart := False;
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -NonInteractive -Command "if (Get-Process blender -ErrorAction SilentlyContinue) { exit 5 } else { exit 0 }"',
    '', SW_HIDE, ewWaitUntilTerminated, ExitCode) then begin
    Result := 'Could not check whether Blender is running. Close Blender and retry.';
  end else if ExitCode = 5 then begin
    Result := 'Close Blender before installing StudioFree MoCap. Your unsaved Blender work will not be closed automatically.';
  end else if ExitCode <> 0 then begin
    Result := 'Blender process check failed. Close Blender and retry.';
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ExitCode: Integer;
  Params: String;
begin
  if CurStep <> ssPostInstall then Exit;
  WizardForm.StatusLabel.Caption := 'Verifying and installing StudioFree MoCap components...';
  Params := '"' + ExpandConstant('{app}\payload\maintenance\studiofree_maintenance.py') + '" apply' +
    ' --payload "' + ExpandConstant('{app}\payload') + '"' +
    ' --program-root "' + ExpandConstant('{localappdata}\Programs\VTA') + '"' +
    ' --data-root "' + ExpandConstant('{localappdata}\VTA') + '"' +
    ' --blender "' + BlenderPage.Values[0] + '"';
  if (not Exec(ExpandConstant('{app}\payload\control\python.exe'), Params, '', SW_HIDE, ewWaitUntilTerminated, ExitCode)) or (ExitCode <> 0) then begin
    MsgBox('StudioFree MoCap installation did not finish. The previous active release was kept. Rerun this installer to repair or retry. See %LOCALAPPDATA%\VTA\logs\maintenance.jsonl for details.', mbError, MB_OK);
    RaiseException('StudioFree MoCap maintenance failed.');
  end;
end;

function InitializeUninstall(): Boolean;
var
  ExitCode: Integer;
  PythonExe: String;
  Params: String;
begin
  Result := False;
  if not RegQueryStringValue(HKCU, 'Software\StudioFree\MoCap', 'InstallerPython', PythonExe) then begin
    MsgBox('The StudioFree control Python path is missing. Reinstall StudioFree before uninstalling.', mbError, MB_OK);
    Exit;
  end;
  if not FileExists(PythonExe) then begin
    MsgBox('The StudioFree control runtime is missing. Repair StudioFree before uninstalling.', mbError, MB_OK);
    Exit;
  end;
  Params := '"' + ExpandConstant('{app}\payload\maintenance\studiofree_maintenance.py') + '" uninstall' +
    ' --program-root "' + ExpandConstant('{localappdata}\Programs\VTA') + '"' +
    ' --data-root "' + ExpandConstant('{localappdata}\VTA') + '"';
  if (not Exec(PythonExe, Params, '', SW_HIDE, ewWaitUntilTerminated, ExitCode)) or (ExitCode <> 0) then begin
    MsgBox('StudioFree cleanup failed. Application files were kept for repair. See %LOCALAPPDATA%\VTA\logs\maintenance.jsonl.', mbError, MB_OK);
    Exit;
  end;
  Result := True;
end;
