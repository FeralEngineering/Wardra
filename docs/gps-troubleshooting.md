# GPS Troubleshooting

## Problem: GPS shows valid but logs never change

This can happen if `gpsd` is holding the last valid fix instead of updating.

Symptoms:

- UI shows GPS OK
- mode shows 2 or 3
- stale shows false
- coordinates remain unchanged over long drives

Check:

```bash
cgps -s
```

or:

```bash
gpspipe -w
```

Watch for:

- changing latitude
- changing longitude
- changing speed
- satellite count

If values remain static:

Restart gpsd:

```bash
sudo systemctl restart gpsd
```

Verify the USB GPS device:

```bash
ls /dev/ttyUSB*
```

Check service:

```bash
sudo systemctl status gpsd
```

---

## Problem: No GPS fix

Possible causes:

- poor sky visibility
- cold start
- insufficient satellites

Allow several minutes for first lock.

---

## Problem: Altitude looks wrong

Altitude can be noisy or incorrect even with a valid 3D fix.

This is normal.

Wardra primarily depends on:

- latitude
- longitude
- speed
- fix mode
