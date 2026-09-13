@echo off
setlocal
cd /d "%~dp0"

if exist ".tools\jdk\jdk-17.0.20.1+1\bin\java.exe" set "JAVA_HOME=%CD%\.tools\jdk\jdk-17.0.20.1+1"
if exist ".tools\android-sdk\platforms\android-34\android.jar" set "ANDROID_SDK_ROOT=%CD%\.tools\android-sdk"
if exist "%ANDROID_SDK_ROOT%" set "ANDROID_HOME=%ANDROID_SDK_ROOT%"

call android\gradlew.bat -p android assembleDebug %*
if errorlevel 1 exit /b %errorlevel%
echo.
echo APK: %CD%\android\app\build\outputs\apk\debug\app-debug.apk
