; ============================================================================
; LocalScribe — Inno Setup Installer Script
; ============================================================================
;
; Prerequisites:
;   1. Build the app first:  python build.py
;   2. Install Inno Setup:   https://jrsoftware.org/isdl.php
;   3. Compile this script:  right-click installer.iss → Compile
;      Or from CLI:  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
;
; Output:  dist\LocalScribe_Setup.exe
; ============================================================================

#define MyAppName      "LocalScribe"
#define MyAppVersion   "1.0.0"
#define MyAppPublisher "shenfurkan"
#define MyAppURL       "https://github.com/shenfurkan/LocalScribe"
#define MyAppExeName   "LocalScribe.exe"

[Setup]
; Unique GUID — must remain constant across versions so Windows
; recognises this as an upgrade rather than a separate install.
AppId={{B7E3F2A1-4C8D-4E9F-A1B2-3C4D5E6F7A8B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
; {autopf} resolves to "C:\Program Files" (admin) or
; "%LOCALAPPDATA%\Programs" (non-admin) depending on privileges.
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=dist
OutputBaseFilename=LocalScribe_Setup
SetupIconFile=image\LocalScribe.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Allow non-admin installation so the app can be installed per-user
; without requiring UAC elevation.  Users can still choose admin mode.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Bundle the entire PyInstaller --onedir output into the install directory.
; This includes the exe, _internal/ (with bundled Python + data files),
; and all compiled .pyc / .pyd dependencies.
Source: "dist\LocalScribe\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
; ── User data directories ──────────────────────────────────────────────
; These live OUTSIDE the install folder ({app}) so they are not touched
; during upgrades or uninstalls.  The "uninsneveruninstall" flag tells
; the uninstaller to leave these directories intact even if they are
; empty, so that:
;   1. Downloaded models (~3 GB) survive reinstalls.
;   2. User transcripts are never accidentally deleted.
;   3. setup_state.json persists so the app knows setup was completed.
;
; Ref: https://jrsoftware.org/ishelp/topic_dirssection.htm
Name: "{localappdata}\LocalScribe"; Flags: uninsneveruninstall
Name: "{localappdata}\LocalScribe\models"; Flags: uninsneveruninstall
Name: "{localappdata}\LocalScribe\transcripts"; Flags: uninsneveruninstall

[Icons]
; Start Menu shortcuts.
Name: "{group}\{#MyAppName}";  Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
; Desktop shortcut (opt-in via the "Additional Icons" task above).
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Offer to launch the app immediately after installation completes.
; "nowait" prevents the installer from blocking on the launched process.
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
