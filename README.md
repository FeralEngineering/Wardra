# Wardra
*Wardra is a Raspberry Pi e-paper wardriving virtual pet that evolves as it discovers unique open Wi-Fi networks, logging passive scan metadata and GPS context while displaying a tiny creature on a low-power e-ink screen.*

---

Built on a Pi Zero 2 W with a Waveshare 2.13" e-paper display and GPS, Wardra passively scans nearby wireless access points, logs metadata, tracks movement, and displays a living creature whose mood and evolution reflect what it encounters.

It does **not** connect to networks, attempt authentication, capture traffic, or attack devices.

Wardra is part utility, part artifact, part creature.

---

## Features

- Passive Wi-Fi scanning via `iw`
- GPS logging through `gpsd`
- E-paper creature UI with mood states
- Evolution based on unique open networks discovered
- Persistent logs and state tracking
- Sprite-based animation system
- Low-power, self-contained Raspberry Pi hardware

---

## Hardware

- Raspberry Pi Zero 2 W
- Waveshare 2.13" V3 e-paper display
- USB GPS receiver (G-Mouse)
- microSD card
- portable power source

---

## File Structure

```text
wardra/
├── wardra.py
├── sprites/
├── logs/
└── README.md
```

---

## Installation

Clone the repo:

```bash
git clone https://github.com/FeralEngineering/wardra.git
cd wardra
```

Install dependencies:

```bash
pip install pillow
sudo apt install gpsd gpsd-clients python3-pil
```

Install Waveshare e-paper library and update the import path in `wardra.py` if needed.

---

## Running

```bash
python3 wardra.py
```

Wardra expects to run from:

```text
~/wardra
```

By default it creates:

```text
~/wardra/logs/
```

for scan logs and state tracking.

---

## Running as a Service

A sample systemd service file is included at:

```text
https://github.com/FeralEngineering/Wardra/systemd/wardra.service
```
---

## Logging

Wardra writes:

- `open_networks.jsonl`
- `secure_networks.jsonl`
- `all_networks.jsonl`
- `state.json`

Logs contain Wi-Fi metadata and GPS coordinates.

Do not publish unsanitized logs.

```md

## Troubleshooting

See:

- [GPS troubleshooting](https://github.com/FeralEnigneering/Wardra/docs/gps-troubleshooting.md)
```
---

## Philosophy

Wardra is not designed for efficient data collection.

It is designed to make movement, attention, signal density, and digital geography feel alive.

Its purpose is as much experiential as technical.

---

## License

MIT
