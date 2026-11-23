# Genmon Generator Monitor — Home Assistant Integration

A native Home Assistant custom component for monitoring and controlling Generac generators via [genmon](https://github.com/jgyates/genmon).

This integration communicates with the **genhalink** addon running on your genmon instance, providing real-time generator data through a REST + WebSocket API. It works alongside the existing MQTT-based integration — users can choose whichever fits their setup.

## Features

- **60+ sensors**: Battery voltage, RPM, output power, fuel level, run hours, weather, and more
- **8 binary sensors**: Outage detection, generator running, alarm active, utility present, etc.
- **5 control buttons**: Start, Stop, Start Transfer, Start Exercise, Set Time
- **Quiet mode switch**: Toggle quiet mode for liquid-cooled Evolution controllers
- **Exercise scheduling**: Select day/frequency and set hour/minute via HA UI
- **Automatic discovery**: Zeroconf/mDNS finds your generator on the network
- **WebSocket push updates**: Near-instant state changes without polling delay
- **Marketplace / HACS**: Install and update from the Home Assistant Marketplace (HACS on Home Assistant 2026.10 or older)
- **Diagnostics**: Built-in diagnostics download for troubleshooting
- **Multi-instance**: Supports multiple generators

## Requirements

- **genmon** running on a Raspberry Pi (or similar) connected to your Generac generator
- **genhalink addon** enabled in genmon (see setup below)
- **Home Assistant** 2024.11.0 or later

## Installation

### Option 1: Marketplace / HACS (Recommended)

[![Open your Home Assistant instance and open a repository in the Marketplace.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=jgyates&repository=genmon-ha&category=integration)

The button opens Genmon in the Marketplace (Home Assistant 2026.11 or newer) or in HACS (older versions). If Genmon isn't listed yet, Home Assistant offers to add the repository first.

**Home Assistant 2026.11 or newer (Marketplace)**

1. Click the button above, or go to **Settings** → **Marketplace** and search for "Genmon Generator Monitor"
2. Select **Install**, check the version, and select **Install** again
3. The integration setup opens right away (no restart needed for the first install) — continue with [Setup](#setup)

**Home Assistant 2026.10 or older (HACS)**

1. Open HACS in Home Assistant
2. Click the three dots menu → **Custom repositories**
3. Add `https://github.com/jgyates/genmon-ha` with category **Integration**
4. Search for "Genmon Generator Monitor" and click **Download**
5. Restart Home Assistant

### Option 2: Manual

1. Copy the `https://github.com/jgyates/genmon-ha/custom_components/genmon/` folder to your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant

## Setup

### Step 1: Enable the genhalink addon in genmon

1. Open the genmon web UI (typically `http://<pi-ip>:8000`)
2. Go to **Settings** (gear icon)
3. Find **Home Assistant Integration (Native)** and enable it
4. An **API Key** is automatically generated — copy it for the next step
5. Optional: Enable **Zeroconf** for automatic discovery
6. Click **Save** and the addon will start automatically

### Step 2: Add the integration in Home Assistant

#### Automatic Discovery (if Zeroconf is enabled)
1. Home Assistant will automatically discover your generator
2. A notification will appear — click **Configure**
3. Enter the **API Key** from Step 1
4. Click **Submit**

#### Manual Setup

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=genmon)

1. Click the button above, or go to **Settings** → **Devices & Services** → **Add Integration**
2. Search for **Genmon**
3. Enter:
   - **Host**: IP address of your genmon Raspberry Pi
   - **Port**: `9083` (default)
   - **API Key**: The key from Step 1
4. Click **Submit**

## Options

After setup, you can configure options via the integration's **Configure** button:

| Option | Default | Description |
|--------|---------|-------------|
| Polling interval | 5 seconds | Fallback polling rate. WebSocket push updates are always active. |
| Include monitor stats | Yes | Include CPU usage, comm stats, and other platform metrics |
| Include weather | Yes | Include weather sensors (if configured in genmon) |
| Excluded sensor paths | (empty) | Comma-separated genmon data paths to skip |

## Entities

### Sensors
| Entity | Description | Device Class |
|--------|-------------|-------------|
| Battery Voltage | Generator battery voltage | voltage |
| RPM | Engine RPM | — |
| Output Frequency | Generator output frequency | frequency |
| Output Voltage | Generator output voltage | voltage |
| Output Current | Generator output current | current |
| Output Power | Single-phase output power | power |
| Power Leg 1/2 | Per-leg power (split-phase) | power |
| Current Leg 1/2 | Per-leg current (split-phase) | current |
| Engine State | Current engine state text | — |
| Switch State | Transfer switch position | — |
| Utility Voltage | Incoming utility voltage | voltage |
| Fuel Level | Tank fuel level (%) | — |
| Estimated Fuel | Gallons remaining | — |
| Total Run Hours | Lifetime run hours | duration |
| Energy (30 Days) | kWh generated in last 30 days | energy |
| Weather Temperature | Local weather temp | temperature |
| *...and many more* | Dynamic sensors auto-discovered from your controller | — |

### Binary Sensors
| Entity | Description |
|--------|-------------|
| System In Outage | Utility power lost |
| Generator Running | Engine is running |
| Exercising | Currently in exercise mode |
| Alarm Active | Active alarm present |
| Utility Power Present | Utility power available |
| Switch in Auto | Transfer switch in auto mode |
| Update Available | Genmon update available |

### Controls
| Entity | Type | Description |
|--------|------|-------------|
| Start / Stop | Button | Remote start/stop generator |
| Start Transfer | Button | Start with transfer |
| Start Exercise | Button | Trigger exercise cycle |
| Set Generator Time | Button | Sync generator clock |
| Quiet Mode | Switch | Toggle quiet mode (liquid-cooled Evolution) |
| Exercise Frequency | Select | Weekly; BiWeekly/Monthly on controllers that support them |
| Exercise Day of Week | Select | Day of the week for weekly and biweekly exercise |
| Exercise Day of Month | Number | Day of the month for monthly exercise (controllers that support it) |
| Exercise Hour/Minute | Number | Time of day for exercise |
| Controller buttons | Button | Buttons the controller defines (e.g. Reset Maintenance); buttons that need a passcode use the Button passcode option |
| Controller settings | Number | Controller values set through its buttons (e.g. Exercise Duration, Start-up Delay on liquid-cooled Evolution), limited to the range the controller accepts. Shows the last value set from Home Assistant |

## Native vs MQTT Integration

| Feature | Native (genhalink) | MQTT (genhomeassistant) |
|---------|-------------------|------------------------|
| Setup | Direct connection | Requires MQTT broker |
| Discovery | Zeroconf automatic | MQTT Discovery |
| Updates | WebSocket push + polling | MQTT publish on change |
| Latency | ~instant | Depends on broker |
| Dependencies | genhalink addon | MQTT broker + addon |
| Config flow | Full HA config UI | Manual MQTT setup |
| Diagnostics | Built-in | — |

Both integrations work independently. You can use either or both simultaneously.

## Troubleshooting

### Cannot connect
- Verify genmon is running: `http://<pi-ip>:8000`
- Verify genhalink addon is enabled in genmon Settings
- Check the port (default `9083`) is accessible from your HA instance
- Check firewall rules on the Pi: `sudo ufw allow 9083/tcp`

### Invalid API key
- If the key changes after setup, Home Assistant shows a "Reauthentication required" notification for the integration; enter the new key there
- The current key is in genmon Settings → Home Assistant Integration (Native)

### Missing entities
- Some entities are controller-specific (Evolution, Nexus, H-100, PowerZone)
- Check Options → "Include monitor stats" / "Include weather" toggles
- Dynamic sensors appear after the first data poll

### Check genhalink logs
On the genmon Pi:
```bash
tail -f /var/log/genhalink.log
```

## Development

The tests use [pytest-homeassistant-custom-component](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component) (Python 3.14):

```bash
python3.14 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest
```

The GitHub CI also runs them on the minimum Home Assistant version (2024.11, Python 3.12) with `requirements_test_min.txt`.

They run against real genhalink answers in `tests/fixtures/<controller>/`, captured from genmon's generator simulator. To capture them again after genhalink or its entity definitions change (needs a checkout of the [genmon](https://github.com/jgyates/genmon) repository):

```bash
python3 tests/capture_fixtures.py ../genmon
```

## License

This integration is part of the [genmon](https://github.com/jgyates/genmon) project and is distributed under the same license, GPL-2.0 (see [LICENSE](LICENSE)).
