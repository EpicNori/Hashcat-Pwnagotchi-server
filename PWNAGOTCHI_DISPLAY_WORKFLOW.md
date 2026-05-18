# Pwnagotchi Display Workflow

This note records the exact workflow used to make the Hashcat WPA plugin show on a connected Jayofelony Pwnagotchi display.

## Working Device Details

- USB network adapter on Windows: `Raspberry Pi USB Remote NDIS Network Device #2`
- Windows host USB IP: `10.0.0.1` and `10.12.194.3`
- Pwnagotchi device IP that responded: `10.12.194.1`
- Pwnagotchi web UI: `http://10.12.194.1:8080`
- Bettercap web UI: `http://10.12.194.1`
- Live rendered display image: `http://10.12.194.1:8080/ui`

The firmware exposes the current Pwnagotchi face as a PNG at `/ui`. Pulling that image is the fastest way to verify whether display changes actually work.

## Prompt For Next Time

Use this when continuing this type of work:

```text
We are editing a Pwnagotchi plugin for Jayofelony Pwnagotchi. The connected device is reachable over USB at 10.12.194.1, with the Pwnagotchi web UI on http://10.12.194.1:8080 and the live display image at http://10.12.194.1:8080/ui. Before guessing, pull /ui and inspect the real rendered screen. Check /plugins to confirm the plugin is installed and enabled, then check /plugins/pwnagotchi_hashcat_wpa to see whether the device is running the expected plugin page.

For display text, keep it simple and visible. Use pwnagotchi.ui.components.LabeledValue, pwnagotchi.ui.fonts, and BLACK from pwnagotchi.ui.view. Register the element in on_ui_setup(), update it in on_ui_update(), and remove it in on_unload(). On this firmware and layout, position (8, 84) appears in the lower-left band under BT:Trusted and above the bottom divider line. A single line works better than a multi-line block. The confirmed display is: label "HWP", value "SET URL", "READY", or "QUEUED N".

If the device still shows old code after pressing Upgrade in the plugin manager, bump both __version__ in pwnagotchi_hashcat_wpa.py and version in pwnagotchi_hashcat_wpa.toml, push to GitHub, run the plugin upgrade from the web UI with the CSRF session cookie, then restart in AUTO mode. Verify again by fetching /ui.
```

## Commands Used

Find the device:

```powershell
Test-NetConnection 10.12.194.1 -Port 22
Test-NetConnection 10.12.194.1 -Port 8080
Test-NetConnection 10.12.194.1 -Port 80
```

Fetch the live display:

```powershell
Invoke-WebRequest -UseBasicParsing -Uri "http://10.12.194.1:8080/ui?x=$(Get-Random)" -OutFile "$env:TEMP\pwnagotchi-ui.png" -TimeoutSec 15
```

Upgrade the plugin through the Pwnagotchi web UI:

```powershell
$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$page = Invoke-WebRequest -UseBasicParsing -Uri http://10.12.194.1:8080/plugins -WebSession $session -TimeoutSec 10
$token = [regex]::Match($page.Content, 'name="csrf_token" value="([^"]+)"').Groups[1].Value
Invoke-WebRequest -UseBasicParsing -Uri http://10.12.194.1:8080/plugins/upgrade -Method POST -WebSession $session -Body @{
    plugin = 'pwnagotchi_hashcat_wpa'
    upgrade = 'Upgrade'
    csrf_token = $token
} -TimeoutSec 90
```

Restart Pwnagotchi in AUTO mode:

```powershell
$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$page = Invoke-WebRequest -UseBasicParsing -Uri http://10.12.194.1:8080/ -WebSession $session -TimeoutSec 10
$token = [regex]::Match($page.Content, 'name="csrf_token" value="([^"]+)"').Groups[1].Value
Invoke-WebRequest -UseBasicParsing -Uri http://10.12.194.1:8080/restart -Method POST -WebSession $session -Body @{
    mode = 'AUTO'
    csrf_token = $token
} -TimeoutSec 30
```

## Confirmed Display Code Pattern

```python
from pwnagotchi.ui.components import LabeledValue
from pwnagotchi.ui.view import BLACK
import pwnagotchi.ui.fonts as fonts

def on_ui_setup(self, ui):
    ui.add_element(
        self._display_key,
        LabeledValue(
            label='HWP',
            value=self._display_value(),
            position=(8, 84),
            label_font=fonts.Bold,
            text_font=fonts.Medium,
            color=BLACK,
        ),
    )

def on_ui_update(self, ui):
    ui.set(self._display_key, self._display_value())

def on_unload(self, ui):
    ui.remove_element(self._display_key)
```

## Result

The display now shows one simple line in the lower-left blank area:

```text
HWP SET URL
```

After the server URL, username, and password are configured, it should show:

```text
HWP READY
```

When files are waiting to upload, it should show:

```text
HWP QUEUED N
```
