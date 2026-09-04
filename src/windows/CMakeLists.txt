# MPCASU — Windows Qt desktop player (M-PCASU).
# Single-player pipeline: UI -> CppPlaybackController -> PlaybackBackend
# (LibVLCBackend) -> VideoSurface. Links the shared casu libraries and the
# bundled libVLC import library; stages Qt/VLC runtimes next to the exe so
# Wine runs it directly from the build tree.
#
# No AUTOMOC: the bundled Qt is Windows-only (moc.exe cannot run on the
# Linux build host), so the app declares no Q_OBJECT/signals of its own and
# uses std::function callbacks + QMetaObject::invokeMethod queued functors.
find_package(Qt6 REQUIRED COMPONENTS Widgets Gui Core Network)
add_executable(casu_mpcasu
    main.cpp
    theme.cpp
    video_surface.cpp
    playlist.cpp
    settings.cpp
    library.cpp
    epg.cpp
    visualizer.cpp
    recording.cpp
    youtube_proxy.cpp
    web_player_tabs.cpp
    main_window.cpp
)
target_link_libraries(casu_mpcasu PRIVATE
    casu_playback casu_media casu_network casu_codec casu_core
    Qt6::Widgets Qt6::Gui Qt6::Core Qt6::Network
    "${CASU_THIRD_PARTY}/vlc/libvlc.a")

# Embedded web-player tabs: support Microsoft Edge WebView2 on Windows (MinGW & MSVC)
# and QtWebEngine when available.
set(WEBVIEW2_INCLUDE_DIR "${CASU_THIRD_PARTY}/webview2/include")
set(WEBVIEW2_LOADER_DLL "${CASU_THIRD_PARTY}/webview2/x64/WebView2Loader.dll")
if(EXISTS "${WEBVIEW2_INCLUDE_DIR}/WebView2.h" AND EXISTS "${WEBVIEW2_LOADER_DLL}")
  message(STATUS "MPCASU: Microsoft Edge WebView2 found — embedded web-player tabs ENABLED via WebView2")
  target_include_directories(casu_mpcasu PRIVATE "${WEBVIEW2_INCLUDE_DIR}")
  target_compile_definitions(casu_mpcasu PRIVATE CASU_HAVE_WEBVIEW2=1)
  target_link_libraries(casu_mpcasu PRIVATE ole32 uuid)
  if(CMAKE_CROSSCOMPILING)
    add_custom_command(TARGET casu_mpcasu POST_BUILD
      COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "${WEBVIEW2_LOADER_DLL}"
        $<TARGET_FILE_DIR:casu_mpcasu>
      COMMAND_EXPAND_LISTS)
  endif()
endif()

find_package(Qt6 COMPONENTS WebEngineWidgets QUIET)
if(Qt6WebEngineWidgets_FOUND)
  message(STATUS "MPCASU: QtWebEngine found — embedded web-player tabs ENABLED")
  target_link_libraries(casu_mpcasu PRIVATE Qt6::WebEngineWidgets)
  target_compile_definitions(casu_mpcasu PRIVATE CASU_HAVE_WEBENGINE=1)
  set_target_properties(casu_mpcasu PROPERTIES AUTOMOC ON AUTOUIC ON)
  if(NOT CMAKE_CROSSCOMPILING)
    get_target_property(_qwe_bin Qt6::WebEngineWidgets IMPORTED_LOCATION)
    get_filename_component(_qt_bin_dir "${_qwe_bin}" DIRECTORY)
    add_custom_command(TARGET casu_mpcasu POST_BUILD
      COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "${_qt_bin_dir}/QtWebEngineProcess.exe"
        $<TARGET_FILE_DIR:casu_mpcasu>
      COMMAND ${CMAKE_COMMAND} -E copy_directory
        "${_qt_bin_dir}/../resources"
        "$<TARGET_FILE_DIR:casu_mpcasu>/resources"
      COMMAND ${CMAKE_COMMAND} -E copy_directory
        "${_qt_bin_dir}/../translations/qtwebengine_locales"
        "$<TARGET_FILE_DIR:casu_mpcasu>/translations/qtwebengine_locales"
      COMMAND_EXPAND_LISTS)
  endif()
endif()

target_include_directories(casu_mpcasu PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
target_compile_features(casu_mpcasu PRIVATE cxx_std_20)
# GUI subsystem: no console window next to the player. (stdout still works
# when redirected, so the Wine smoke harness keeps its markers.)
set_target_properties(casu_mpcasu PROPERTIES RUNTIME_OUTPUT_NAME "MPCASU" WIN32_EXECUTABLE TRUE)
casu_stage_app("MPCASU.exe" casu_mpcasu)

# Application icon resource (embedded into the exe; loaded via IDI_ICON1).
find_program(MINGW_WINDRES x86_64-w64-mingw32-windres)
if(MINGW_WINDRES)
  add_custom_command(OUTPUT ${CMAKE_CURRENT_BINARY_DIR}/app_res.o
    COMMAND ${MINGW_WINDRES}
      ${CMAKE_CURRENT_SOURCE_DIR}/app.rc -O coff
      -o ${CMAKE_CURRENT_BINARY_DIR}/app_res.o
    DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/app.rc
            ${CMAKE_CURRENT_SOURCE_DIR}/assets/mpcasu_player_icon.ico)
  target_sources(casu_mpcasu PRIVATE ${CMAKE_CURRENT_BINARY_DIR}/app_res.o)
endif()

# Brand assets next to the exe (build tree AND packaged zip): branch icons,
# combo arrow, header logo, player icon, web icon. Mirrors the Linux
# repo-level assets/ directory (theme.py resolves them __file__-relative;
# this port resolves them applicationDirPath()-relative).
add_custom_command(TARGET casu_mpcasu POST_BUILD
  COMMAND ${CMAKE_COMMAND} -E copy_directory
    ${CMAKE_CURRENT_SOURCE_DIR}/assets
    $<TARGET_FILE_DIR:casu_mpcasu>/assets
  COMMAND_EXPAND_LISTS)
# Trailing slash: contents land directly in <pkg>/assets (no assets/assets).
install(DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/assets/
        DESTINATION assets COMPONENT assets)

if(CMAKE_CROSSCOMPILING)
  # Stage Qt + MinGW runtime DLLs next to the exe.
  add_custom_command(TARGET casu_mpcasu POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
      $<TARGET_RUNTIME_DLLS:casu_mpcasu>
      $<TARGET_FILE_DIR:casu_mpcasu>
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
      "${QT_BIN_DIR}/libgcc_s_seh-1.dll"
      "${QT_BIN_DIR}/libstdc++-6.dll"
      "${QT_BIN_DIR}/libwinpthread-1.dll"
      "${QT_BIN_DIR}/libssl-3-x64.dll"
      "${QT_BIN_DIR}/libcrypto-3-x64.dll"
      $<TARGET_FILE_DIR:casu_mpcasu>
    COMMAND_EXPAND_LISTS)

  # Qt platform + image plugins are required by a QWidget app under Wine.
  add_custom_command(TARGET casu_mpcasu POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_directory
      "${QT_BIN_DIR}/../plugins/platforms"
      "$<TARGET_FILE_DIR:casu_mpcasu>/platforms"
    COMMAND ${CMAKE_COMMAND} -E copy_directory
      "${QT_BIN_DIR}/../plugins/imageformats"
      "$<TARGET_FILE_DIR:casu_mpcasu>/imageformats"
    COMMAND ${CMAKE_COMMAND} -E copy_directory
      "${QT_BIN_DIR}/../plugins/styles"
      "$<TARGET_FILE_DIR:casu_mpcasu>/styles"
    COMMAND_EXPAND_LISTS)

  # libVLC runtime next to the exe (libvlc.dll + libvlccore.dll + plugins).
  add_custom_command(TARGET casu_mpcasu POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
      "${CASU_THIRD_PARTY}/vlc/libvlc.dll"
      "${CASU_THIRD_PARTY}/vlc/libvlccore.dll"
      $<TARGET_FILE_DIR:casu_mpcasu>
    COMMAND ${CMAKE_COMMAND} -E copy_directory
      "${CASU_THIRD_PARTY}/vlc/plugins"
      "$<TARGET_FILE_DIR:casu_mpcasu>/plugins"
    COMMAND_EXPAND_LISTS)

  # Bundled helper tools (ffmpeg/ffprobe/yt-dlp) for the app's feature pages.
  add_custom_command(TARGET casu_mpcasu POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E make_directory
      "$<TARGET_FILE_DIR:casu_mpcasu>/tools"
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
      "${CASU_THIRD_PARTY}/tools/ffmpeg.exe"
      "${CASU_THIRD_PARTY}/tools/ffprobe.exe"
      "${CASU_THIRD_PARTY}/tools/yt-dlp.exe"
      "$<TARGET_FILE_DIR:casu_mpcasu>/tools"
    COMMAND_EXPAND_LISTS)
endif()
