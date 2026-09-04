Unicode True
!include "MUI2.nsh"

!define PRODUCT "MPCASU Player"
!define VERSION "7.0.0"
!define STAGE "..\..\dist\windows\MPCASU-Player"

Name "${PRODUCT} ${VERSION}"
OutFile "..\..\dist\MPCASU-Player-Setup-7.0.0.exe"
InstallDir "$LOCALAPPDATA\MPCASU Player"
RequestExecutionLevel user

!insertmacro MUI_PAGE_LICENSE "..\..\LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Section "MPCASU Player" SecMain
  SetOutPath "$INSTDIR"
  File /r "${STAGE}\*.*"
  CreateDirectory "$SMPROGRAMS\MPCASU Player"
  CreateShortcut "$SMPROGRAMS\MPCASU Player\MPCASU Player.lnk" "$INSTDIR\MPCASU.exe"
  CreateShortcut "$SMPROGRAMS\MPCASU Player\Uninstall.lnk" "$INSTDIR\Uninstall.exe"
  CreateShortcut "$DESKTOP\MPCASU Player.lnk" "$INSTDIR\MPCASU.exe"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MPCASU Player" "DisplayName" "${PRODUCT} ${VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MPCASU Player" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "Software\Classes\.casu" "" "MPCASU.Player.Container"
  WriteRegStr HKCU "Software\Classes\.mp5" "" "MPCASU.Player.Container"
  WriteRegStr HKCU "Software\Classes\MPCASU.Player.Container\shell\open\command" "" '"$INSTDIR\MPCASU.exe" "%1"'
SectionEnd

Section "Uninstall"
  Delete "$DESKTOP\MPCASU Player.lnk"
  Delete "$SMPROGRAMS\MPCASU Player\MPCASU Player.lnk"
  Delete "$SMPROGRAMS\MPCASU Player\Uninstall.lnk"
  RMDir "$SMPROGRAMS\MPCASU Player"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MPCASU Player"
  DeleteRegKey HKCU "Software\Classes\MPCASU.Player.Container"
  RMDir /r "$INSTDIR"
SectionEnd
