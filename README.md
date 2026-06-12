# Wardra

![Wardra running](./images/wardra-live.JPG)

Wardra is a Raspberry Pi-powered wardriving virtual pet.

It passively scans nearby Wi-Fi access points, logs basic metadata with GPS context, and displays a small e-paper creature whose mood and evolution change as it discovers new networks.

Wardra does **not** connect to networks, attempt authentication, capture traffic, crack passwords, or attack devices.

It is part field logger, part Tamagotchi, part little signal-hungry goblin.

---

## Features

- Passive Wi-Fi scanning with `iw`
- GPS tracking through `gpsd`
- Waveshare 2.13" e-paper display support
- Sprite-based creature UI
- Mood states: base, alert, excited, bored, sleep
- Evolution based on unique open Wi-Fi access points discovered
- Persistent JSONL logs
- Persistent state tracking
- Single-instance lock to prevent duplicate service runs
- Partial e-paper refresh support to reduce flashing
- Optional systemd service file for always-on device use

---

## Hardware

Wardra was built around:

- Raspberry Pi Zero 2 W
- Waveshare 2.13" V3 e-paper display
- USB GPS receiver / G-Mouse
- microSD card
- Portable USB power source

For full hardware details, see [Hardware](./docs/hardware.md)

---

## Project Structure

```text
wardra/
├── wardra.py
├── sprites/
├── docs/
│   └── gps-troubleshooting.md
├── systemd/
│   └── wardra.service
├── .gitignore
├── LICENSE
└── README.md
```

Wardra expects its working folder to contain:

```text
wardra.py
sprites/
logs/
```

The `logs/` folder is created automatically when the script runs.

---

## Installation

Clone the repo:

```bash
git clone https://github.com/FeralEngineering/wardra.git
cd wardra
```

Install Python dependency:

```bash
pip install -r requirements.txt
```

Install system packages:

```bash
sudo apt install gpsd gpsd-clients python3-pil
```

Wardra also requires the Waveshare e-Paper Python library.

The current script uses this path:

```python
sys.path.append("/home/wardra/e-Paper/RaspberryPi_JetsonNano/python/lib")
```

If your Waveshare library is installed somewhere else, update that line in `wardra.py`.

---

## Running

```bash
python3 wardra.py
```

The current code uses:

```python
BASE_DIR = os.path.expanduser("~/wardra")
```

For the default `wardra` user, that means:

```text
/home/wardra/wardra
```

---

## Running as a Service

A sample systemd service file is included here:

[systemd/wardra.service](./systemd/wardra.service)

It assumes Wardra is installed at:

```text
/home/wardra/wardra
```

and runs as the `wardra` user.

---

## Logs

Wardra writes JSONL logs to:

```text
~/wardra/logs/
```

Generated files include:

- `open_networks.jsonl`
- `secure_networks.jsonl`
- `all_networks.jsonl`
- `state.json`
- `wardra.lock`

Example outputs:

- [Sample open network log](./examples/sample_open_networks.jsonl)
- [Sample secure network log](./examples/sample_secure_networks.jsonl)
- [Sample state file](./examples/sample_state.json)

---

## Evolution

Wardra currently evolves based on the number of unique open Wi-Fi BSSIDs discovered.

Current thresholds:

```python
EVOLVE_THRESHOLDS = [333, 666, 999, 1312]
```

Stages are determined by open-network discovery count, not by connecting to networks.

---

## GPS Troubleshooting

See:

[GPS Troubleshooting](./docs/gps-troubleshooting.md)

---

## Philosophy

Wardra is built around the idea that not everything has to be efficient to be meaningful.

The point is not to build the most powerful scanner possible.

The point is to feel less like a tool and more like a creature you carry through digital terrain.

---

## License

MIT
