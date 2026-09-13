# LanLink

LanLink is a first Android-to-laptop connection prototype for devices on the
same Wi-Fi network.

## What is included

- `laptop/server.py`: zero-dependency Python companion server.
- `laptop/web/index.html`: laptop-side message window.
- `android/`: native Kotlin Android app source.

The first slice intentionally focuses on a small, testable loop: pair with a
six-digit code, keep a polling connection alive, and send text in both
directions. File transfer and automatic device discovery can be added on top
of this protocol later.

## Try it

1. On the laptop, run `laptop/run_server.bat` or `python laptop/server.py`.
2. Note the printed Wi-Fi address (chọn IP cùng dải mạng với điện thoại) and pairing code.
3. Open `android/` in Android Studio, sync Gradle, and install the app on an
   Android phone.
4. Put the laptop address (for example `192.168.1.10:8765`) and code into the
   app, then tap **Kết nối**.
5. Send a message from either the phone or the laptop browser UI.

If the phone cannot connect, check that both devices are on the same Wi-Fi,
the laptop address is not `127.0.0.1`, the Wi-Fi does not isolate clients, and
Windows Firewall allows Python on a Private network.

The ready-to-install debug APK is `LanLink-debug.apk`. To rebuild it from the
command line on a machine with Android SDK/JDK configured:

```powershell
cd D:\App\LanLink\android
\.\gradlew.bat assembleDebug
```

The output is written to `android/app/build/outputs/apk/debug/app-debug.apk`.

## Android build prerequisites

The Gradle project includes its wrapper files. A build machine needs Android
Studio (or an Android SDK and JDK) with API 34 installed. The debug APK in this
folder was built and verified with Gradle 8.9, compile SDK 34, and Kotlin 1.9.24.
