# WeighScale — Weighbridge-to-SAP Integration

End-to-end integration that reads weight data from an industrial weighbridge connected via RS-232/USB serial, exposes it as a REST API, and consumes it from SAP ABAP.

```
┌─────────────┐  RS-232/USB  ┌──────────────────────┐  HTTP/JSON  ┌───────────────┐
│ Weighbridge │─────────────▶│  Python Service       │◀───────────│  SAP ABAP     │
│ (COM port)  │              │  (pyserial + Flask +  │            │  (CL_HTTP_    │
│             │              │   Waitress)           │            │   CLIENT)     │
└─────────────┘              └──────────────────────┘            └───────────────┘
```

## Project Files

| File | Purpose |
|------|---------|
| `scale_service.py` | Production service — reads serial port, serves REST API |
| `scale_win_service.py` | Wraps the service as a Windows NT service (pywin32) |
| `mock_weighbridge.py` | Simulates a scale on a virtual COM port (com0com) |
| `scale_service_mock.py` | Pure-HTTP mock — no serial port or driver needed |
| `zcl_weigh_scale_client.abap` | SAP ABAP class — calls GET /weight, parses JSON |
| `zweigh_scale_test.abap` | SAP ABAP test report — displays weight reading |
| `requirements.txt` | Python dependencies |

---

## Prerequisites

### Python Side (Windows)
- Python 3.10+ (64-bit recommended)
- `pip install -r requirements.txt`
- For the Windows service: `pip install pywin32` then `python Scripts/pywin32_postinstall.py -install`

### SAP Side
- SAP NetWeaver 7.40 SP02+ or S/4HANA (for `/UI2/CL_JSON`)
- Outbound HTTP enabled in ICM (transaction `SMICM` → Services)
- If using HTTPS: server certificate imported via `STRUST`

---

## Data Contract

All services (real and mock) return identical JSON:

### GET /weight
```json
{
  "weight": 12345.67,
  "unit":   "kg",
  "stable": true,
  "raw":    "ST,GS,+000012345.67,kg"
}
```

### GET /health
```json
{ "status": "ok" }
```
The mock adds `"mode": "mock"` to the health response.

### Optional API-Key Authentication
Set `API_KEY` in the Python service config. Every request must then include:
```
X-API-Key: <your-key>
```

---

## Testing Modes

### Mode 1 — Fastest (No Hardware, No Drivers)

Use `scale_service_mock.py` which simulates the weighbridge lifecycle entirely in memory.

```bash
# Terminal 1: start the mock service
python scale_service_mock.py

# Terminal 2: test with curl
curl http://localhost:8080/health
curl http://localhost:8080/weight

# SAP: run ZWEIGH_SCALE_TEST with p_url = http://<windows-pc-ip>:8080
```

The mock cycles through realistic vehicle phases (tare → ramp-up → settle → stable → drive-off → pause) so you will see `stable` toggling between `true` and `false`.

---

### Mode 2 — Realistic Serial (No Physical Hardware)

Uses **com0com** to create a virtual serial port pair. One script writes simulated scale data; the other reads it — exactly as the real service would.

#### Step 1: Install com0com
1. Download from [com0com on SourceForge](https://sourceforge.net/projects/com0com/)
2. Run the Setup utility and create a pair: **COM3 ↔ COM4**
   - COM3 = the port `scale_service.py` reads from
   - COM4 = the port `mock_weighbridge.py` writes to

#### Step 2: Run the mock weighbridge writer
```bash
# Terminal 1: simulate the scale (writes to COM4)
python mock_weighbridge.py --port COM4
```

#### Step 3: Run the real service (reads COM3)
```bash
# Terminal 2: the production service reads COM3
python scale_service.py
```

#### Step 4: Test
```bash
# Terminal 3:
curl http://localhost:8080/health
curl http://localhost:8080/weight
```

You should see the weight values changing as mock_weighbridge.py cycles through vehicle simulations.

---

### Mode 3 — Production (Real Weighbridge)

#### Step 1: Connect the weighbridge
- Connect the scale to the Windows PC via RS-232 or USB-serial adapter
- Note the COM port number (Device Manager → Ports)

#### Step 2: Configure
Edit the constants at the top of `scale_service.py`:
```python
SERIAL_PORT  = "COM3"     # Your actual COM port
BAUD_RATE    = 9600       # Match your scale's setting
```

#### Step 3: Install as a Windows service
```cmd
:: Run as Administrator
python scale_win_service.py install
python scale_win_service.py start
```

Verify:
```bash
curl http://localhost:8080/health
curl http://localhost:8080/weight
```

Manage the service:
```cmd
python scale_win_service.py stop
python scale_win_service.py remove
```

Or use `services.msc` to start/stop/configure auto-start.

#### Step 4: SAP Configuration
See the SAP Setup section below.

---

## SAP Setup

### Option A: Direct URL (dev/test)

1. Create the ABAP objects in your system:
   - Create class `ZCL_WEIGH_SCALE_CLIENT` from `zcl_weigh_scale_client.abap`
   - Create exception class `ZCX_WEIGH_SCALE_ERROR` (or use the inline definition)
   - Create report `ZWEIGH_SCALE_TEST` from `zweigh_scale_test.abap`

2. Run `ZWEIGH_SCALE_TEST` with:
   - `p_url` = `http://<windows-pc-ip>:8080`
   - Leave `p_dest` blank

### Option B: SM59 Destination (production — recommended)

1. Transaction **SM59** → Create → Connection Type **G** (HTTP to External Server)
   - RFC Destination: `ZWEIGH_SCALE` (or your naming convention)
   - Target Host: `<windows-pc-ip>`
   - Service No (port): `8080`
   - Path Prefix: leave blank (the ABAP client appends `/weight`)
   - If using HTTPS: activate SSL and assign the certificate from STRUST

2. Test the connection in SM59 (click "Connection Test")

3. Run `ZWEIGH_SCALE_TEST` with:
   - `p_dest` = `ZWEIGH_SCALE`
   - Leave `p_url` at default (it will be ignored)

### HTTPS / Certificate Notes

If you configure the Python service behind an HTTPS reverse proxy (e.g. nginx, IIS ARR):

1. **STRUST** → Import the server's SSL certificate into the *SSL Client (Anonymous)* PSE
2. Set ICM parameter `icm/HTTPS/client_sni_enabled = TRUE` in profile if SNI is required
3. Restart ICM via `SMICM` → Administration → ICM → Restart

### Firewall / Network

- The SAP application server must be able to reach the Windows PC on port 8080 (or your configured port)
- Check SICF → Client → Proxy settings if your SAP system uses an outbound proxy

---

## Configuration Reference

### scale_service.py / scale_service_mock.py

| Variable | Default | Description |
|----------|---------|-------------|
| `SERIAL_PORT` | `COM3` | COM port the scale is connected to |
| `BAUD_RATE` | `9600` | Serial baud rate |
| `HTTP_HOST` | `0.0.0.0` | Bind address |
| `HTTP_PORT` | `8080` | REST API port |
| `API_KEY` | `None` | Set to a string to require X-API-Key |
| `LOG_FILE` | `scale_service.log` | Log file path |
| `LOG_LEVEL` | `INFO` | Python log level |
| `RECONNECT_BASE_DELAY` | `2` | Initial reconnect delay (seconds) |
| `RECONNECT_MAX_DELAY` | `60` | Maximum reconnect delay (seconds) |

### mock_weighbridge.py

| Variable | Default | Description |
|----------|---------|-------------|
| `WRITE_PORT` | `COM4` | COM port to write simulated data to |
| `BAUD_RATE` | `9600` | Must match the reader's baud rate |
| `MIN_GROSS_KG` | `5000` | Minimum simulated vehicle weight |
| `MAX_GROSS_KG` | `42000` | Maximum simulated vehicle weight |
| `LINE_INTERVAL` | `0.5` | Seconds between simulated readings |

---

## Scale Protocol

### Default Format
```
ST,GS,+000012345.67,kg\r\n
```

| Token | Meaning |
|-------|---------|
| `ST` | Stable reading |
| `US` | Unstable reading |
| `GS` | Gross weight |
| `NT` | Net weight (tare subtracted) |
| `+/-NNN.NN` | Signed decimal weight |
| `kg` / `lb` / `t` | Unit |

### Parsing

The regex in `scale_service.py` → `parse_scale_line()`:
```python
r"(ST|US),[A-Z]{2},([+-]?\d+\.?\d*),(\w+)"
```

To adapt to a different vendor, modify this regex. See the inline comments in `scale_service.py` for guidance on Mettler Toledo SICS, Avery Weigh-Tronix, and Sartorius formats.

---

## What to Customize for Your Specific Scale Model

- [ ] **COM port and baud rate** — check your scale's manual and Device Manager
- [ ] **Line format / regex** — update `_SCALE_LINE_RE` in `scale_service.py` to match your scale's ASCII protocol (see inline vendor notes)
- [ ] **Stability token** — some scales use `S`/`D` instead of `ST`/`US`; update the `parse_scale_line()` function accordingly
- [ ] **Data bits / parity / stop bits** — some scales use 7E1 or 7O1; adjust `DATA_BITS`, `PARITY`, `STOP_BITS`
- [ ] **Unit string** — your scale may report `lb`, `t`, or omit the unit entirely; adapt the parser
- [ ] **HTTP port** — change `HTTP_PORT` if 8080 conflicts with another service
- [ ] **API key** — set `API_KEY` to a secure value in production; update the ABAP client's `iv_api_key`
- [ ] **Log file path** — point `LOG_FILE` to a location with sufficient disk space and rotation (consider using Python's `RotatingFileHandler` for long-running production use)
- [ ] **ABAP field mapping** — if your JSON keys differ from `weight`/`unit`/`stable`/`raw`, adjust the ABAP structure `ty_weight_reading` and the `/UI2/CL_JSON` pretty-name mode
- [ ] **Extension point** — implement the business logic in `zcl_weigh_scale_client→get_weight()` to log readings, update deliveries, or post material documents

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `curl` returns "Connection refused" | Service not running or wrong port | Start the service; check `HTTP_PORT` |
| Serial port errors in log | Wrong COM port or port in use | Check Device Manager; close other apps using the port |
| ABAP "HTTP send failed" | SAP can't reach the PC | Check firewall, proxy, ICM outbound settings |
| ABAP "HTTP 401 Unauthorized" | API key mismatch | Verify `API_KEY` matches `iv_api_key` |
| JSON parsing returns empty fields | Field name mismatch | Check `/UI2/CL_JSON` pretty_name mode; compare JSON keys to ABAP structure |
| "No reading available yet" | Service started but no scale data received | Wait for the first reading; check serial connection |

---

## License

MIT
