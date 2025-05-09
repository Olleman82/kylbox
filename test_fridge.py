import asyncio
from bleak import BleakClient, BleakError
import struct
import sys
import argparse

ADDRESS     = "07:4D:FB:A7:C4:5E"   # WT-0001
WRITE_CHAR  = "00001235-0000-1000-8000-00805f9b34fb"
NOTIFY_CHAR = "00001236-0000-1000-8000-00805f9b34fb"

def calculate_checksum(data: bytes) -> int:
    """Beräknar checksumma för ett meddelande"""
    return sum(data) & 0xFF

def create_packet(data: bytes) -> bytes:
    """Skapar ett paket med header, längd, payload och 2-byte checksum."""
    pkt = b'\xFE\xFE' + struct.pack('B', len(data) + 2) + data
    csum = sum(pkt) & 0xFFFF
    pkt += struct.pack('>H', csum)
    return pkt

def parse_frame(data: bytearray):
    # enkel ram-parser
    length  = data[2]
    cmd     = data[3]
    payload = data[4:4+length-3]
    return cmd, payload

def notification_handler(_, data):
    cmd, payload = parse_frame(data)
    if cmd == 0x01:         # query-svar
        # Konvertera till signed int8
        temp = struct.unpack('b', bytes([payload[14]]))[0]  # 'b' för signed char
        target = struct.unpack('b', bytes([payload[4]]))[0]  # target temperature
        bat_percent = payload[15]  # battery percentage
        bat_vol = f"{payload[16]}.{payload[17]}"  # battery voltage
        print(f"Aktuell temp: {temp}°C")
        print(f"Måltemperatur: {target}°C")
        print(f"Batteri: {bat_percent}% ({bat_vol}V)")
        print(f"Debug - payload (Query): {[hex(x) for x in payload]}")
    elif cmd == 0x05:       # set-temperature bekräftelse (eko av skickat kommando)
        print("Bekräftelse (eko) mottagen för Set Temperature")
        print(f"Debug - payload (Set Confirm/Echo): {[hex(x) for x in payload]}") # Skriv ut payload för set-bekräftelse

async def connect_with_retry(max_attempts=3):
    for attempt in range(max_attempts):
        try:
            print(f"Försöker ansluta (försök {attempt + 1}/{max_attempts})...")
            client = BleakClient(ADDRESS)
            await client.connect()
            print("Ansluten!")
            return client
        except BleakError as e:
            print(f"Anslutningsfel: {e}")
            if attempt < max_attempts - 1:
                print("Väntar 5 sekunder innan nästa försök...")
                await asyncio.sleep(5)
            else:
                print("Kunde inte ansluta efter alla försök.")
                sys.exit(1)

async def set_temperature(client, temp: int):
    """Sätter önskad temperatur"""
    # Kommando 0x06 (setRight) för att sätta temperatur
    # Hypotes: Enzon mappas internt till höger zon
    # Format: fe fe 03 06 TT 02 CC (Baserat på README exempel för 0x05)
    # TT = temperatur (signed byte)
    # CC = checksum
    temp_byte = struct.pack('b', temp)[0]  # Konvertera till signed byte
    # Skapar paket med kommando 0x05 (SetUnit1Target) och temperatur
    data = bytes([0x05, temp_byte])
    command = create_packet(data)
    
    print(f"Skickar kommando: {[hex(x) for x in command]}") # Debug: visa kommandot som skickas
    print(f"Sätter temperatur till {temp}°C...")
    await client.write_gatt_char(WRITE_CHAR, command)
    # Vänta på bekräftelse (som nu hanteras i notification_handler)
    await asyncio.sleep(2)
    
    # Verifiera att temperaturen har ändrats
    print("Verifierar temperaturändring...")
    await client.write_gatt_char(WRITE_CHAR, b'\xfe\xfe\x03\x01\x02\x00')
    await asyncio.sleep(2)

async def main():
    parser = argparse.ArgumentParser(description='Styr kylboxen')
    parser.add_argument('-t', '--temp', type=int, help='Sätt temperatur (°C)')
    args = parser.parse_args()

    client = await connect_with_retry()
    try:
        await client.start_notify(NOTIFY_CHAR, notification_handler)
        # print("Skickar bind-kommando...")
        # # bind
        # await client.write_gatt_char(WRITE_CHAR, b'\xfe\xfe\x03\x00\x01\xff')
        # await asyncio.sleep(2)

        if args.temp is not None:
            await set_temperature(client, args.temp)
        else:
            print("Läser av status...")
            # skicka en query
            await client.write_gatt_char(WRITE_CHAR, b'\xfe\xfe\x03\x01\x02\x00')
            # vänta på notifiering
            await asyncio.sleep(2)

    except Exception as e:
        print(f"Ett fel uppstod: {e}")
    finally:
        await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main()) 