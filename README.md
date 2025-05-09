# Vevor Kylbox Styrning

Ett Python-program för att styra Vevor kylbox via Bluetooth.

## Funktioner

- Läsa av aktuell temperatur
- Sätta önskad temperatur
- Visa batteristatus
- Bluetooth-kommunikation via BLE

## Installation

1. Skapa en virtuell miljö:
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# eller
venv\Scripts\activate  # Windows
```

2. Installera beroenden:
```bash
pip install bleak
```

## Användning

```bash
# Läs av status
python test_fridge.py

# Sätt temperatur (exempel: 5°C)
python test_fridge.py -t 5
```

## Bluetooth-specifikationer

- Enhet: WT-0001
- MAC-adress: 07:4D:FB:A7:C4:5E
- Write Characteristic: 00001235-0000-1000-8000-00805f9b34fb
- Notify Characteristic: 00001236-0000-1000-8000-00805f9b34fb 