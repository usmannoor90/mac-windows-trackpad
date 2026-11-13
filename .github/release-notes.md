## Install

1. Download **Instant-Release-*.zip** below and double-click it to unzip.
2. Drag **Instant Release.app** into your **Applications** folder and open it.
3. macOS will say the app is from an unidentified developer. Open **System Settings → Privacy & Security**, scroll down and click **Open Anyway**. (This is a one-time step; the app is open source and not registered with Apple's paid developer program.)
4. Click **Allow** when asked for Accessibility access, or turn on **Instant Release** in System Settings → Privacy & Security → Accessibility.

Or, in Terminal, which does all of the above in one go:

```sh
curl -fsSL https://raw.githubusercontent.com/usmannoor90/mac-windows-trackpad/main/install-app.sh | bash
```

Then turn **off** macOS's own *Tap to click* (Trackpad settings) and *Use trackpad for dragging* (Accessibility → Pointer Control → Trackpad Options). The app's menu bar icon warns you if either is still on.
