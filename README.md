# Wardra

![Wardra running](images/wardra-live.JPG)

Wardra is a Raspberry Pi-based wardriving virtual pet.

It passively scans for nearby Wi-Fi networks, logs them with GPS data, and uses those discoveries to drive an evolution system displayed on an e-paper screen.

Wardra does not connect to networks, capture traffic, or perform attacks.

---

## Features

- Passive Wi-Fi scanning with `iw`
- GPS tracking through `gpsd`
- Waveshare 2.13" e-paper display support
- Sprite-based UI with multiple states
- Evolution based on unique open Wi-Fi networks discovered
- Hand-drawn sprite sets for each evolution stage and behavior state
- Persistent JSONL logging
- Persistent state tracking
- Single-instance lock file
- Partial e-paper refresh support
- Optional systemd service support

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
Wardra/
├── wardra.py
├── requirements.txt
├── README.md
├── LICENSE
├── .gitignore
├── sprites/
│   ├── splash.png
│   ├── stage1_*.png
│   ├── stage2_*.png
│   ├── stage3_*.png
│   └── stage4_*.png
├── images/
│   └── wardra-live.JPG
├── docs/
│   ├── hardware.md
│   └── gps-troubleshooting.md
├── examples/
│   ├── sample_open_networks.jsonl
│   ├── sample_secure_networks.jsonl
│   └── sample_state.json
└── systemd/
    └── wardra.service
```

Wardra expects its working directory to contain:

```text
wardra.py
sprites/
logs/
```

The `logs/` directory is created automatically.

---

## Installation

Clone the repo:

```bash
git clone https://github.com/FeralEngineering/Wardra.git
cd Wardra
```

Install Python dependencies:

```bash
pip install -r requirements.txt
```

Install required system packages:

```bash
sudo apt install gpsd gpsd-clients python3-pil
```

Wardra also requires the Waveshare e-Paper Python library.

The current script uses:

```python
sys.path.append("/home/wardra/e-Paper/RaspberryPi_JetsonNano/python/lib")
```

Update that path if your installation differs.

---

## Running

```bash
python3 wardra.py
```

Current code uses:

```python
BASE_DIR = os.path.expanduser("~/wardra")
```

Default path:

```text
/home/wardra/wardra
```

---

## Running as a Service

A sample systemd service file is included:

[systemd/wardra.service](./systemd/wardra.service)

It assumes:

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

Files include:

- `open_networks.jsonl`
- `secure_networks.jsonl`
- `all_networks.jsonl`
- `state.json`
- `wardra.lock`

Examples:

- [Sample open network log](./examples/sample_open_networks.jsonl)
- [Sample secure network log](./examples/sample_secure_networks.jsonl)
- [Sample state file](./examples/sample_state.json)

---

## Evolution System

Wardra tracks unique open Wi-Fi BSSIDs.

Evolution stages are based on discovery count.

Current thresholds:

```python
EVOLVE_THRESHOLDS = [333, 666, 999, 1312]
```

---

## GPS Troubleshooting

See:

[GPS Troubleshooting](./docs/gps-troubleshooting.md)

---

## License

MIT
