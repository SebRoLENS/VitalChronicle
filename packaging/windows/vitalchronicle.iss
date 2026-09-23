#ifndef MyAppVersion
  #error MyAppVersion must be passed to ISCC
#endif
#ifndef SourceExe
  #error SourceExe must be passed to ISCC
#endif
#ifndef RootDir
  #error RootDir must be passed to ISCC
#endif
#ifndef OutputDir
  #define OutputDir RootDir
#endif

[Setup]
AppId={{8FBD240E-321B-465D-AC09-E09DF357B563}
AppName=VitalChronicle
AppVersion={#MyAppVersion}
AppPublisher=Sebastiano Romi
AppPublisherURL=https://github.com/SebRoLENS/VitalChronicle
AppUpdatesURL=https://github.com/SebRoLENS/VitalChronicle/releases/latest
DefaultDirName={localappdata}\Programs\VitalChronicle
DefaultGroupName=VitalChronicle
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=VitalChronicle-{#MyAppVersion}-windows-x86_64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile={#RootDir}\generated-icons\vitalchronicle.ico
UninstallDisplayIcon={app}\VitalChronicle.exe
CloseApplications=yes
RestartApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceExe}"; DestDir: "{app}"; DestName: "VitalChronicle.exe"; Flags: ignoreversion
Source: "{#RootDir}\packaging\windows\vitalchronicle-installed.marker"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\VitalChronicle"; Filename: "{app}\VitalChronicle.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\VitalChronicle"; Filename: "{app}\VitalChronicle.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\VitalChronicle.exe"; Description: "{cm:LaunchProgram,VitalChronicle}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent
