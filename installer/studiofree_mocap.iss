; StudioFree MoCap per-user offline installer (Inno Setup 7.x).
; Distribute inside one ZIP beside its verified payload/ directory.
; The Python maintenance script uses the packaged offline control runtime.
; It never reads or removes project data.

#ifndef ProductVersion
  #error ProductVersion is required
#endif
#ifndef ExpectedPackageSHA256
  #error ExpectedPackageSHA256 is required
#endif
#if Len(ExpectedPackageSHA256) != 64
  #error ExpectedPackageSHA256 must be a SHA-256 hex digest
#endif
#if !defined(InternalValidation) && !defined(SignToolName)
  #error Customer installers require Authenticode signing
#endif
#define VerifierSHA256 GetSHA256OfFile("verify_sidecar.ps1")

[Setup]
#ifdef InternalValidation
AppId={{A83B7D2A-0AD5-4B37-A30B-57429D193E29}
AppName=StudioFree MoCap INTERNAL TEST
#else
AppId={{D377EBD6-3302-4AC4-A49E-7F4247FE2321}
AppName=StudioFree MoCap
#endif
AppVersion={#ProductVersion}
AppPublisher=Arcomade
#ifdef InternalValidation
DefaultDirName={localappdata}\Programs\VTA-InternalTest\Installer
#else
DefaultDirName={localappdata}\Programs\VTA\Installer
#endif
DisableDirPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
#ifdef InternalValidation
OutputBaseFilename=StudioFreeMoCap-{#ProductVersion}-INTERNAL-TEST
#elif defined(SignToolName)
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
UsePreviousAppDir=no
#ifdef SignToolName
SignTool={#SignToolName}
SignedUninstaller=yes
#endif

[Files]
Source: "verify_sidecar.ps1"; DestDir: "{app}"; Flags: ignoreversion noencryption
Source: "{src}\payload\*"; DestDir: "{app}\payload"; Flags: external recursesubdirs createallsubdirs ignoreversion

[InstallDelete]
; A full replacement must remove A-only files before copying payload B.
Type: filesandordirs; Name: "{app}\payload"

[Registry]
#ifdef InternalValidation
Root: HKA; Subkey: "Software\StudioFree\MoCap-InternalTest"; ValueType: string; ValueName: "InstallerPython"; ValueData: "{app}\payload\control\python.exe"; Flags: uninsdeletevalue
#else
Root: HKA; Subkey: "Software\StudioFree\MoCap"; ValueType: string; ValueName: "InstallerPython"; ValueData: "{app}\payload\control\python.exe"; Flags: uninsdeletevalue
#endif

[Code]
var
  BlenderPage: TInputFileWizardPage;

function InitializeSetup(): Boolean;
begin
  Result := True;
#ifdef InternalValidation
  Result := (GetEnv('STUDIOFREE_INTERNAL_VALIDATION') = '1') and
            (GetEnv('BLENDER_USER_CONFIG') <> '') and
            (GetEnv('BLENDER_USER_EXTENSIONS') <> '');
  if not Result then
    MsgBox('INTERNAL TEST installer requires isolated Blender profile environment variables.', mbError, MB_OK);
#endif
end;

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
  Params: String;
begin
  Result := '';
  NeedsRestart := False;
  ExtractTemporaryFile('verify_sidecar.ps1');
  Params := '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
    ExpandConstant('{tmp}\verify_sidecar.ps1') + '" -InstallTarget "' +
    ExpandConstant('{app}\payload') + '"';
  if (not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
      Params, '', SW_HIDE, ewWaitUntilTerminated, ExitCode)) or (ExitCode <> 0) then begin
    Result := 'Installation target contains a link or cannot be validated. Choose a clean StudioFree installation directory.';
    Exit;
  end;
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
  WizardForm.StatusLabel.Caption := 'Authenticating StudioFree MoCap package...';
  ExtractTemporaryFile('verify_sidecar.ps1');
  Params := '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
    ExpandConstant('{tmp}\verify_sidecar.ps1') + '" -Payload "' +
    ExpandConstant('{app}\payload') + '" -ExpectedManifestSha256 "{#ExpectedPackageSHA256}"';
  if (not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
      Params, '', SW_HIDE, ewWaitUntilTerminated, ExitCode)) or (ExitCode <> 0) then begin
    MsgBox('StudioFree MoCap package authentication failed. No package code was started. Re-extract the complete distribution ZIP and retry.', mbError, MB_OK);
    RaiseException('StudioFree MoCap package authentication failed.');
  end;
  WizardForm.StatusLabel.Caption := 'Verifying and installing StudioFree MoCap components...';
  Params := '"' + ExpandConstant('{app}\payload\maintenance\studiofree_maintenance.py') + '" apply' +
    ' --payload "' + ExpandConstant('{app}\payload') + '"' +
#ifdef InternalValidation
    ' --program-root "' + ExpandConstant('{localappdata}\Programs\VTA-InternalTest') + '"' +
    ' --data-root "' + ExpandConstant('{localappdata}\VTA-InternalTest') + '"' +
    ' --internal-validation' +
#else
    ' --program-root "' + ExpandConstant('{localappdata}\Programs\VTA') + '"' +
    ' --data-root "' + ExpandConstant('{localappdata}\VTA') + '"' +
#endif
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
  if not FileExists(ExpandConstant('{app}\verify_sidecar.ps1')) or
      not SameText(GetSHA256OfFile(ExpandConstant('{app}\verify_sidecar.ps1')), '{#VerifierSHA256}') then begin
    MsgBox('The installed package verifier is missing or changed. Repair StudioFree before uninstalling.', mbError, MB_OK);
    Exit;
  end;
  Params := '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
    ExpandConstant('{app}\verify_sidecar.ps1') + '" -Payload "' +
    ExpandConstant('{app}\payload') + '" -ExpectedManifestSha256 "{#ExpectedPackageSHA256}"';
  if (not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
      Params, '', SW_HIDE, ewWaitUntilTerminated, ExitCode)) or (ExitCode <> 0) then begin
    MsgBox('Installed package authentication failed. No package code was started. Repair StudioFree before uninstalling.', mbError, MB_OK);
    Exit;
  end;
#ifdef InternalValidation
  if not RegQueryStringValue(HKCU, 'Software\StudioFree\MoCap-InternalTest', 'InstallerPython', PythonExe) then begin
#else
  if not RegQueryStringValue(HKCU, 'Software\StudioFree\MoCap', 'InstallerPython', PythonExe) then begin
#endif
    MsgBox('The StudioFree control Python path is missing. Reinstall StudioFree before uninstalling.', mbError, MB_OK);
    Exit;
  end;
  if not FileExists(PythonExe) then begin
    MsgBox('The StudioFree control runtime is missing. Repair StudioFree before uninstalling.', mbError, MB_OK);
    Exit;
  end;
  Params := '"' + ExpandConstant('{app}\payload\maintenance\studiofree_maintenance.py') + '" uninstall' +
#ifdef InternalValidation
    ' --program-root "' + ExpandConstant('{localappdata}\Programs\VTA-InternalTest') + '"' +
    ' --data-root "' + ExpandConstant('{localappdata}\VTA-InternalTest') + '"';
#else
    ' --program-root "' + ExpandConstant('{localappdata}\Programs\VTA') + '"' +
    ' --data-root "' + ExpandConstant('{localappdata}\VTA') + '"';
#endif
  if (not Exec(PythonExe, Params, '', SW_HIDE, ewWaitUntilTerminated, ExitCode)) or (ExitCode <> 0) then begin
    MsgBox('StudioFree cleanup failed. Application files were kept for repair. See %LOCALAPPDATA%\VTA\logs\maintenance.jsonl.', mbError, MB_OK);
    Exit;
  end;
  Result := True;
end;
